import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://host.docker.internal:8000';
const PRODUCT_IDS = (__ENV.PRODUCT_IDS || '1,2,3')
  .split(',')
  .map((value) => Number(value.trim()))
  .filter((value) => Number.isInteger(value) && value > 0);

if (PRODUCT_IDS.length === 0) {
  throw new Error('PRODUCT_IDS deve conter ao menos um ID válido.');
}

export const options = {
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
  summaryTimeUnit: 'ms',

  scenarios: {
    c0m_moderada: {
      executor: 'ramping-arrival-rate',
      startRate: 1,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 150,
      gracefulStop: '30s',

      stages: [
        { target: 10, duration: '20s' },
        { target: 20, duration: '30s' },
        { target: 30, duration: '30s' },
        { target: 0, duration: '10s' },
      ],

      tags: {
        test_group: 'C0m',
        load_profile: 'moderada',
      },
    },
  },

  thresholds: {
    http_req_failed: ['rate<0.10'],
    http_req_duration: ['p(95)<1500'],
    checks: ['rate>0.95'],
  },
};

export default function () {
  const productId = PRODUCT_IDS[Math.floor(Math.random() * PRODUCT_IDS.length)];

  const payload = JSON.stringify({
    product_id: productId,
    quantity: 1,
  });

  const response = http.post(`${BASE_URL}/checkout`, payload, {
    headers: {
      'Content-Type': 'application/json',
    },
    timeout: '60s',
    tags: {
      name: 'POST /checkout',
      test_group: 'C0m',
      load_profile: 'moderada',
    },
  });

  let body = null;

  try {
    body = response.json();
  } catch (_) {
    // O check abaixo registrará a resposta como JSON inválido.
  }

  check(response, {
    'status HTTP 200, 201 ou 202': (res) =>
      res.status === 200 || res.status === 201 || res.status === 202,

    'resposta JSON válida': () => body !== null,

    'resposta possui identificador do pedido': () =>
      body !== null &&
      (body.order_id !== undefined || body.id !== undefined),
  });
}
