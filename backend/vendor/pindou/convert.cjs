const fs = require('node:fs');
const { convertImageData } = require('./imageToBeads.js');

const input = fs.readFileSync(0);
const headerEnd = input.indexOf(10);
const { side, size, colorLimit, colors } = JSON.parse(input.subarray(0, headerEnd));
const pixels = new Uint8ClampedArray(input.subarray(headerEnd + 1));
const palette = colors.map(([id, r, g, b]) => ({
  id,
  rgb: [r, g, b],
  primaryBrand: 'MARD',
  primaryCode: id,
  codes: { MARD: id },
  group: id[0],
  name: id,
  hex: '#' + [r, g, b].map(value => value.toString(16).padStart(2, '0')).join(''),
}));

const result = convertImageData(pixels, side, side, {
  width: size,
  bounds: { width: size, height: size },
  maxColors: colorLimit ?? palette.length,
  palette,
  backgroundMode: 'keep',
  backgroundColor: [255, 255, 255],
  tolerance: 32,
  speckleReduction: 0,
  generationStyle: 'cartoon',
});
process.stdout.write(JSON.stringify(result.cells));
