import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://host.docker.internal:8000';
const RUN_ID = __ENV.RUN_ID || 'c0a-run-local';
const PRODUCT_IDS = [1, 2, 3];

export const options = {
  summaryTrendStats: [
    'avg',
    'min',
    'med',
    'p(90)',
    'p(95)',
    'p(99)',
    'max',
    'count',
  ],
  scenarios: {
    c0a: {
      executor: 'ramping-arrival-rate',
      startRate: 1,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 300,
      gracefulStop: '30s',
      stages: [
        { target: 20, duration: '20s' },
        { target: 40, duration: '30s' },
        { target: 60, duration: '30s' },
        { target: 0, duration: '10s' },
      ],
      tags: {
        scenario: 'c0a',
        load_profile: 'alta',
        run_id: RUN_ID,
      },
    },
  },
};

export default function () {
  const productId = PRODUCT_IDS[Math.floor(Math.random() * PRODUCT_IDS.length)];
  const expectedQuantity = 1;
  const payload = JSON.stringify({
    product_id: productId,
    quantity: expectedQuantity,
  });

  const response = http.post(`${BASE_URL}/checkout`, payload, {
    headers: {
      'Content-Type': 'application/json',
    },
    timeout: '60s',
    tags: {
      name: 'POST /checkout',
      scenario: 'c0a',
      load_profile: 'alta',
      run_id: RUN_ID,
    },
  });

  let body = null;

  try {
    body = response.json();
  } catch (_) {
    body = null;
  }

  check(response, {
    'status 201': (res) => res.status === 201,
    'resposta JSON valida': () => body !== null,
    'possui ID do pedido': () => body !== null && body.order !== undefined && Number.isInteger(body.order.id),
    'pedido criado como PENDING': () =>
      body !== null && body.order !== undefined && body.order.status === 'PENDING',
    'possui produto e quantidade': () =>
      body !== null &&
      body.order !== undefined &&
      body.order.product_id === productId &&
      body.order.quantity === expectedQuantity,
  });
}
