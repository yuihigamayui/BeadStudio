# PinDou converter

`imageToBeads.js` and `palette.js` are readable CommonJS builds of the
corresponding files in [PinDou](https://github.com/better12138/PinDou) at
commit `2f04f1275ee593bee2aceef72a1a97d75d4b5705`. They are distributed
under the included MIT license.

To refresh the files, run TypeScript on upstream `src/imageToBeads.ts` and
`src/palette.ts` with `--module commonjs --target es2020 --outDir <dir>`.
BeadStudio supplies its own palette and calls the pixel-buffer entry point via
`convert.cjs`; the upstream application and browser dependencies are unused.
