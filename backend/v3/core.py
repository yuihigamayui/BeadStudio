"""BeadStudio V3: GPL-3.0-or-later port of Timothy Gerstner's 2013 PIA.
Copyright (c) 2013 Timothy Gerstner. Full license: LICENSE in this directory.
Original source and prior experiments: archive/BeadStudio_before_V3.zip.
Only the user-selected AUTHOR-SOURCE semantics are active (not paper profile).
Source: https://cragl.cs.gmu.edu/pixelate/journal.html .
Mean smoothed SP initialization; T0=1.1*sqrt(2*PCA eigenvalue); absolute
PCA residuals; nearest-color hard assignment; paired-subcluster MCDA.
Safety differences: float64 reductions, stable probabilities, normalized
floating importance mass, corrected uncovered/empty-region fallback and
condensed index dtype. Not bitwise validated against the OpenCV 2.3 binary.
"""
from pathlib import Path
import ctypes
import json
import subprocess
import shutil
import numpy as np
import cv2
from skimage.color import rgb2lab, deltaE_ciede2000


def _kernel():
    folder = Path(__file__).parent
    cpp, dll = folder/'mapping.cpp', folder/'mapping.dll'
    # Git checkout timestamps are not a reliable indication of source changes.
    if not dll.exists():
        compiler = shutil.which('g++')
        if not compiler:
            raise RuntimeError('Existing MinGW g++ required for the standalone spatial kernel')
        subprocess.run([compiler, '-O3', '-shared', '-static-libgcc', '-static-libstdc++',
                        str(cpp), '-o', str(dll)], check=True, capture_output=True)
    lib = ctypes.CDLL(str(dll))
    fun = lib.pia_mapping
    f32 = np.ctypeslib.ndpointer(dtype=np.float32, flags='C_CONTIGUOUS')
    f64 = np.ctypeslib.ndpointer(dtype=np.float64, flags='C_CONTIGUOUS')
    i32 = np.ctypeslib.ndpointer(dtype=np.int32, flags='C_CONTIGUOUS')
    fun.argtypes = [f32, f64, f64]+[ctypes.c_int]*5+[ctypes.c_double, i32]
    fun.restype = None
    return lib, fun


class PIA:
    """Stateful original PIA algorithm port, including user constraints.

    PIA(rgb, size=48, k=12, importance=None, profile='author').run()
    After convergence: set_importance, set_pixel_constraint(y,x,[indices]),
    set_palette_color(index, lab, locked=True), sample_palette_color(...),
    run(). No reinitialization or reheating; temperature remains Tf=1.
    save(path)/load(path) preserve arrays/constraints/annealing state without pickle.
    """
    def __init__(self, rgb, size=48, k=12, importance=None, profile='author'):
        rgb = np.asarray(rgb, dtype=np.uint8)
        if rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < size or k < 1:
            raise ValueError('Expected composed RGB image >= output dimensions; K>=1')
        if profile != 'author':
            raise ValueError('Unknown fidelity profile')
        self.rgb, self.h, self.w = rgb.copy(), *rgb.shape[:2]
        self.size, self.k, self.n, self.profile = size, k, size*size, profile
        self.lab = cv2.cvtColor(rgb.astype(np.float32)/255, cv2.COLOR_RGB2LAB)
        self.range = int(np.sqrt((self.h/size)*(self.w/size)))
        self.m, self.laplacian, self.sigma, self.saturation = 45., .4, .87, 1.1
        yy, xx = np.indices((self.h, self.w))
        self.xy = np.stack((xx.ravel(), yy.ravel()), axis=1)
        self.regions = ((yy*size//self.h)*size+xx*size//self.w).astype(np.int32)
        gy, gx = np.indices((size, size))
        self.pos = np.stack(((gx.ravel()+.5)*self.w/size, (gy.ravel()+.5)*self.h/size), axis=1)
        self.means = np.zeros((self.n, 3))
        self.importance = np.ones((self.h, self.w))
        self.converged, self.maxed, self.iteration = False, False, 0
        self.temperature = 1.
        self.history = []
        self.allowed = np.ones((self.n, 2), dtype=bool)
        if importance is not None:
            self._validate_importance(importance)
            self.importance = np.asarray(importance, dtype=float).copy()
        self._update_means()
        first = self.means.mean(axis=0)
        self.colors = first[None].astype(float)
        self.prior = np.array([.5, .5])
        self.prob = np.full((self.n, 2), .5)
        vec, val = self._pca(0)
        self.colors = np.array([first, first+.8*vec])
        self.pairs = [(0, 1)]
        self.temperature = max(1., 1.1*np.sqrt(2*val))
        self.initial_temperature = self.temperature
        self.locked = np.zeros(2, dtype=bool)
        self.assign = np.zeros(self.n, dtype=int)
        if k == 1:
            self._condense()

    def _validate_importance(self, importance):
        a = np.asarray(importance)
        if a.shape != (self.h, self.w) or not np.isfinite(a).all() or a.min() < 0 or a.max() > 1 or a.sum() <= 0:
            raise ValueError('Importance must be HxW in [0,1], with nonzero mass')

    def _update_means(self):
        regions = self.regions.ravel()
        count = np.bincount(regions, minlength=self.n)
        valid = count > 0
        for c in range(3):
            sums = np.bincount(regions, weights=self.lab[..., c].ravel(), minlength=self.n)
            self.means[valid, c] = sums[valid]/count[valid]
        for c in range(2):
            sums = np.bincount(regions, weights=self.xy[:, c], minlength=self.n)
            self.pos[valid, c] = sums[valid]/count[valid]
        for s in np.flatnonzero(~valid):
            y, x = divmod(int(s), self.size)
            self.means[s] = self.lab[min(self.h-1, y*self.h//self.size), min(self.w-1, x*self.w//self.size)]
        mass = np.bincount(regions, weights=self.importance.ravel(), minlength=self.n)
        self.sp_prior = mass/np.maximum(count, 1)
        if self.sp_prior.sum() <= 0:
            raise RuntimeError('All importance mass lost')
        self.sp_prior /= self.sp_prior.sum()
        # Pix::SmoothSuperpixelPositions: 4-neighbor arithmetic mean; boundary
        # axes frozen; no extra anchor clipping from the old inspired version.
        p = self.pos.reshape(self.size, self.size, 2)
        padded = np.pad(p, ((1, 1), (1, 1), (0, 0)), mode='constant')
        total = padded[:-2, 1:-1]+padded[2:, 1:-1]+padded[1:-1, :-2]+padded[1:-1, 2:]
        number = np.full((self.size, self.size), 4.)
        number[[0, -1], :] -= 1
        number[:, [0, -1]] -= 1
        smoothed = (1-self.laplacian)*p+self.laplacian*total/number[..., None]
        smoothed[:, [0, -1], 0] = p[:, [0, -1], 0]
        smoothed[[0, -1], :, 1] = p[[0, -1], :, 1]
        self.pos = smoothed.reshape(-1, 2)
        # 3x3 bilateral with truncated (not repeated edge) neighborhoods.
        a = self.means.reshape(self.size, self.size, 3)
        acc, denom = np.zeros_like(a), np.zeros(a.shape[:2])
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ys, xs = slice(max(0, -dy), min(self.size, self.size-dy)), slice(max(0, -dx), min(self.size, self.size-dx))
                yo, xo = slice(max(0, dy), min(self.size, self.size+dy)), slice(max(0, dx), min(self.size, self.size+dx))
                other, center = a[yo, xo], a[ys, xs]
                weight = np.exp(-(np.sum((center-other)**2, axis=2)+dx*dx+dy*dy)/(2*self.sigma**2))
                acc[ys, xs] += other*weight[..., None]
                denom[ys, xs] += weight
        self.means = (acc/denom[..., None]).reshape(-1, 3)

    def _pca(self, index):
        prior = max(self.prior[index], 1e-300)
        weights = self.prob[:, index]/self.n/prior
        residual = self.colors[index]-self.means
        residual = np.abs(residual)
        matrix = (residual*weights[:, None]).T@residual
        values, vectors = np.linalg.eigh(matrix)
        vector = vectors[:, -1]
        # Eigenvectors have arbitrary sign: deterministic canonical orientation.
        if vector[np.argmax(np.abs(vector))] < 0:
            vector = -vector
        return vector, abs(float(values[-1]))

    def _averaged(self):
        colors = self.colors.copy()
        if not self.maxed:
            for i, j in self.pairs:
                mass = self.prior[i]+self.prior[j]
                color = (self.prior[i]*colors[i]+self.prior[j]*colors[j])/mass if mass > 1e-300 else (colors[i]+colors[j])/2
                colors[i] = colors[j] = color
        return colors

    def _mapping(self):
        if not hasattr(self, '_native'):
            self._native = _kernel()
        self._native[1](np.ascontiguousarray(self.lab, dtype=np.float32), np.ascontiguousarray(self.pos, dtype=np.float64),
                        np.ascontiguousarray(self._averaged()[self.assign], dtype=np.float64),
                        self.w, self.h, self.size, self.size, self.range, self.m, self.regions)

    def _associate(self):
        distance = np.linalg.norm(self.means[:, None]-self.colors[None], axis=2)
        logp = np.log(np.maximum(self.prior, 1e-300))[None]-distance/self.temperature
        logp = np.where(self.allowed, logp, -np.inf)
        logp -= logp.max(axis=1, keepdims=True)
        self.prob = np.exp(logp)
        self.prob /= self.prob.sum(axis=1, keepdims=True)
        self.assign = np.argmin(np.where(self.allowed, distance, np.inf), axis=1)
        self.prior = (self.prob*self.sp_prior[:, None]).sum(axis=0)

    def _refine(self):
        mass = self.prob*self.sp_prior[:, None]
        new = (mass.T@self.means)/np.maximum(self.prior[:, None], 1e-300)
        valid = (~self.locked)&(self.prior > 1e-300)
        error = np.linalg.norm(self.colors[valid]-new[valid], axis=1).sum()
        self.colors[valid] = new[valid]
        return float(error)

    def _split(self, pair):
        i, j = self.pairs[pair]
        end = len(self.colors)
        ci, cj = self.colors[i]+.8*self._pca(i)[0], self.colors[j]+.8*self._pca(j)[0]
        self.colors = np.vstack((self.colors, ci, cj))
        self.prior[[i, j]] *= .5
        self.prior = np.r_[self.prior, self.prior[i], self.prior[j]]
        self.prob = np.column_stack((self.prob, self.prob[:, i], self.prob[:, j]))
        self.locked = np.r_[self.locked, False, False]
        self.allowed = np.column_stack((self.allowed, self.allowed[:, i], self.allowed[:, j]))
        self.pairs[pair] = (i, end)
        self.pairs.append((j, end+1))

    def _condense(self):
        pairs = self.pairs
        averaged = self._averaged()
        self.colors = np.array([averaged[i] for i, _ in pairs])
        self.prior = np.array([self.prior[i]+self.prior[j] for i, j in pairs])
        self.prob = np.column_stack([self.prob[:, i]+self.prob[:, j] for i, j in pairs])
        lookup = np.zeros(2*len(pairs), dtype=int)
        for s, (i, j) in enumerate(pairs):
            lookup[[i, j]] = s
        self.assign = lookup[self.assign]
        self.allowed = np.column_stack([self.allowed[:, i]|self.allowed[:, j] for i, j in pairs])
        self.locked = np.array([self.locked[i] or self.locked[j] for i, j in pairs])
        self.maxed = True

    def _expand(self):
        if self.maxed:
            return
        splits = []
        for p, (i, j) in enumerate(self.pairs):
            error = np.linalg.norm(self.colors[i]-self.colors[j])
            if error > 1.6:
                splits.append((float(error), p))
            else:
                self.colors[j] += .8*self._pca(i)[0]
        for _, p in sorted(splits, reverse=True):
            self._split(p)
            if len(self.pairs) >= self.k:
                self._condense()
                break

    def step(self):
        if self.converged:
            return
        self._mapping()
        self._update_means()
        self._associate()
        error = self._refine()
        before = self.temperature
        if error < 1.:
            if self.temperature <= 1.:
                self.converged = True
            else:
                self.temperature = max(1., self.temperature*.7)
            self._expand()
        self.iteration += 1
        self.history.append((self.iteration, before, error, len(self.colors) if self.maxed else len(self.pairs)))
        if not np.isfinite(self.colors).all():
            raise RuntimeError('Non-finite palette')

    def run(self, max_steps=2000, progress=None):
        for _ in range(max_steps):
            if self.converged:
                # Original UI calls AssociatePalette after convergence too.
                # If fewer than K colors survive, collapse bookkeeping pairs
                # so constraints can refer to logical palette entries as well.
                if not self.maxed:
                    self._condense()
                self._associate()
                return self
            self.step()
            if progress and self.iteration % 25 == 0:
                progress(self)
        raise RuntimeError('PIA did not converge: no result claimed as converged')

    def output(self, saturation=None):
        sat = self.saturation if saturation is None else saturation
        palette = self._averaged()
        palette[:, 1:] *= sat
        rgb = cv2.cvtColor(palette[self.assign].reshape(self.size, self.size, 3).astype(np.float32), cv2.COLOR_LAB2RGB)
        return np.round(np.clip(rgb, 0, 1)*255).astype(np.uint8)

    def bead(self, palette, holes=None):
        # Strictly external final map; no card colors ever enter MCDA.
        rgb = palette[['r', 'g', 'b']].to_numpy(dtype=np.uint8)
        card_lab = rgb2lab(rgb[None]/255.).reshape(-1, 3)
        target = self._averaged()
        target[:, 1:] *= self.saturation
        chosen = deltaE_ciede2000(target[:, None], card_lab[None]).argmin(axis=1)
        index = chosen[self.assign].reshape(self.size, self.size)
        ids = palette.id.to_numpy(dtype=str)[index].astype(object)
        result = rgb[index]
        if holes is not None:
            holes = np.asarray(holes, dtype=bool)
            if holes.shape != ids.shape:
                raise ValueError('Hole mask must match output grid')
            ids[holes] = None
        if len({x for x in ids.flat if x is not None}) > self.k:
            raise RuntimeError('Color budget exceeded')
        return ids, result

    def _editable(self):
        if not self.converged or not self.maxed:
            raise ValueError('Pixel/palette constraints require a converged condensed palette, as in the 2013 UI')

    def set_importance(self, importance):
        self._validate_importance(importance)
        self.importance = np.asarray(importance, dtype=float).copy()
        self.converged = False

    def set_pixel_constraint(self, y, x, allowed):
        if not self.maxed:
            raise ValueError('Apply pixel constraints after automatic convergence')
        if not (0 <= y < self.size and 0 <= x < self.size):
            raise ValueError('Pixel outside output')
        allowed = list(allowed)
        if any(not 0 <= i < len(self.colors) for i in allowed):
            raise ValueError('Unknown palette index')
        row = y*self.size+x
        self.allowed[row] = not bool(allowed)
        self.allowed[row, allowed] = True
        self.converged = False
        self._associate()

    def set_palette_color(self, index, lab=None, locked=True):
        if not self.maxed or not 0 <= index < len(self.colors):
            raise ValueError('Palette must have converged; invalid index')
        if lab is not None:
            value = np.asarray(lab, dtype=float)
            if value.shape != (3,) or not np.isfinite(value).all():
                raise ValueError('Expected finite Lab triple')
            self.colors[index] = value
        self.locked[index] = locked
        self.converged = False
        self._associate()

    def sample_palette_color(self, index, y, x, locked=True):
        self.set_palette_color(index, self.means[y*self.size+x], locked)

    def save(self, path):
        arrays = {key: value for key, value in self.__dict__.items() if isinstance(value, np.ndarray)}
        meta = {key: value for key, value in self.__dict__.items() if isinstance(value, (int, float, bool, str, list))}
        np.savez_compressed(path, metadata=np.array(json.dumps(meta)), **arrays)

    @classmethod
    def load(cls, path):
        state = cls.__new__(cls)
        with np.load(path, allow_pickle=False) as data:
            state.__dict__.update(json.loads(str(data['metadata'])))
            state.__dict__.update({key: data[key].copy() for key in data.files if key != 'metadata'})
        return state
