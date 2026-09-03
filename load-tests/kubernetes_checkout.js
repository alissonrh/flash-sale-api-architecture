import http from 'k6/http';
import { check } from 'k6';
import execution from 'k6/execution';
import { Counter, Trend } from 'k6/metrics';

function integerEnv(name, defaultValue) {
  const rawValue = __ENV[name];

  if (rawValue === undefined || rawValue === '') {
    return defaultValue;
  }

  const parsedValue = Number.parseInt(rawValue, 10);

  if (!Number.isInteger(parsedValue) || parsedValue < 0) {
    throw new Error(`${name} deve ser um inteiro nao negativo`);
  }

  return parsedValue;
}

const BASE_URL = __ENV.BASE_URL || 'http://api:8000';
const RUN_ID = __ENV.RUN_ID || 'c1-kubernetes-local';
const SCENARIO = __ENV.SCENARIO || 'c1';
const PRODUCT_IDS = [1, 2, 3];
const LOAD_STAGES = [
  {
    name: 'stage_1',
    target: integerEnv('STAGE_1_RATE', 20),
    duration: __ENV.STAGE_1_DURATION || '20s',
  },
  {
    name: 'stage_2',
    target: integerEnv('STAGE_2_RATE', 40),
    duration: __ENV.STAGE_2_DURATION || '30s',
  },
  {
    name: 'stage_3',
    target: integerEnv('STAGE_3_RATE', 60),
    duration: __ENV.STAGE_3_DURATION || '30s',
  },
  {
    name: 'stage_4',
    target: integerEnv('STAGE_4_RATE', 0),
    duration: __ENV.STAGE_4_DURATION || '10s',
  },
];

function durationMilliseconds(value) {
  const match = /^(\d+)(ms|s|m)$/.exec(value);

  if (match === null) {
    throw new Error(`duracao invalida: ${value}`);
  }

  const multipliers = { ms: 1, s: 1000, m: 60000 };
  return Number.parseInt(match[1], 10) * multipliers[match[2]];
}

function currentLoadStage() {
  const elapsed = Date.now() - execution.scenario.startTime;
  let boundary = 0;

  for (const stage of LOAD_STAGES) {
    boundary += durationMilliseconds(stage.duration);
    if (elapsed < boundary) {
      return stage.name;
    }
  }

  return LOAD_STAGES[LOAD_STAGES.length - 1].name;
}

const responses2xx = new Counter('responses_2xx');
const responses429 = new Counter('responses_429');
const responses5xx = new Counter('responses_5xx');
const unexpectedStatuses = new Counter('unexpected_statuses');
const connectionErrors = new Counter('connection_errors');
const responseDuration2xx = new Trend('response_duration_2xx', true);
const responseDuration429 = new Trend('response_duration_429', true);

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
    kubernetes_checkout: {
      executor: 'ramping-arrival-rate',
      startRate: integerEnv('START_RATE', 1),
      timeUnit: '1s',
      preAllocatedVUs: integerEnv('PRE_ALLOCATED_VUS', 100),
      maxVUs: integerEnv('MAX_VUS', 300),
      gracefulStop: __ENV.GRACEFUL_STOP || '30s',
      stages: LOAD_STAGES.map(({ target, duration }) => ({ target, duration })),
      tags: {
        scenario: SCENARIO,
        run_id: RUN_ID,
      },
    },
  },
};

export default function () {
  const productId = PRODUCT_IDS[Math.floor(Math.random() * PRODUCT_IDS.length)];
  const expectedQuantity = 1;
  const loadStage = currentLoadStage();
  const metricTags = {
    load_stage: loadStage,
    scenario: SCENARIO,
    run_id: RUN_ID,
  };
  const payload = JSON.stringify({
    product_id: productId,
    quantity: expectedQuantity,
  });

  const response = http.post(`${BASE_URL}/checkout`, payload, {
    headers: {
      'Content-Type': 'application/json',
    },
    timeout: __ENV.REQUEST_TIMEOUT || '60s',
    tags: {
      name: 'POST /checkout',
      ...metricTags,
    },
  });

  if (response.status >= 200 && response.status < 300) {
    responses2xx.add(1, metricTags);
    responseDuration2xx.add(response.timings.duration, metricTags);
  } else if (response.status === 429) {
    responses429.add(1, metricTags);
    responseDuration429.add(response.timings.duration, metricTags);
  } else if (response.status >= 500 && response.status < 600) {
    responses5xx.add(1, metricTags);
  } else if (response.status === 0 || response.error || response.error_code) {
    connectionErrors.add(1, metricTags);
  } else {
    unexpectedStatuses.add(1, metricTags);
  }

  let body = null;

  try {
    body = response.json();
  } catch (_) {
    body = null;
  }

  check(response, {
    'status 201': (res) => res.status === 201,
    'resposta JSON valida': () => body !== null,
    'possui ID do pedido': () =>
      body !== null && body.order !== undefined && Number.isInteger(body.order.id),
    'pedido criado como PENDING': () =>
      body !== null && body.order !== undefined && body.order.status === 'PENDING',
    'possui produto e quantidade': () =>
      body !== null &&
      body.order !== undefined &&
      body.order.product_id === productId &&
      body.order.quantity === expectedQuantity,
  });
}
