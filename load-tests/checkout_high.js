import http from 'k6/http';
import { check } from 'k6';

export const options = {
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],

  scenarios: {
    checkout_high: {
      executor: 'ramping-arrival-rate',
      startRate: 1,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 200,
      stages: [
        { target: 20, duration: '20s' },
        { target: 40, duration: '30s' },
        { target: 60, duration: '30s' },
        { target: 0, duration: '10s' },
      ],
    },
  },

  thresholds: {
    http_req_failed: ['rate<0.10'],
    http_req_duration: ['p(95)<1500'],
    checks: ['rate>0.95'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://host.docker.internal:8000';
const PRODUCT_IDS = [1, 2, 3];

export default function () {
  const productId = PRODUCT_IDS[Math.floor(Math.random() * PRODUCT_IDS.length)];

  const payload = JSON.stringify({
    product_id: productId,
    quantity: 1,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
  };

  const res = http.post(`${BASE_URL}/checkout`, payload, params);

  check(res, {
    'status 201 ou 200': (r) => r.status === 201 || r.status === 200,
    'retornou body com order': (r) => {
      try {
        const body = JSON.parse(r.body);
        return !!body.order?.id;
      } catch (e) {
        return false;
      }
    },
  });
}