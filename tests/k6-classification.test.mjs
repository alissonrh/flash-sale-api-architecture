import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(
  new URL('../load-tests/http-classification.js', import.meta.url),
  'utf8',
);
const classification = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
);
const {
  ACCEPTED_2XX,
  CONNECTION_ERROR,
  RATE_LIMITED_429,
  UNEXPECTED_5XX,
  UNEXPECTED_STATUS,
  classifyResponse,
} = classification;

const cases = [
  [{ status: 200 }, ACCEPTED_2XX],
  [{ status: 201 }, ACCEPTED_2XX],
  [{ status: 429, error: 'response status was 429' }, RATE_LIMITED_429],
  [{ status: 500 }, UNEXPECTED_5XX],
  [{ status: 503, error: 'response status was 503' }, UNEXPECTED_5XX],
  [{ status: 0, error: 'dial tcp' }, CONNECTION_ERROR],
  [{ status: 0, error_code: 1211 }, CONNECTION_ERROR],
  [null, CONNECTION_ERROR],
  [{ status: 301 }, UNEXPECTED_STATUS],
  [{ status: 404 }, UNEXPECTED_STATUS],
];

for (const [response, expected] of cases) {
  assert.equal(classifyResponse(response), expected);
}

console.log(`classificacao k6 validada em ${cases.length} casos`);
