import http from 'k6/http';
import { check } from 'k6';
import execution from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';

import {
  ACCEPTED_2XX,
  CONNECTION_ERROR,
  RATE_LIMITED_429,
  UNEXPECTED_5XX,
  classifyResponse,
} from './http-classification.js';

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

const SCENARIO = __ENV.SCENARIO || 'c1';
const RATE_LIMITED_SCENARIO = SCENARIO === 'c3' || SCENARIO === 'c4';
const BASE_URL =
  __ENV.BASE_URL || (RATE_LIMITED_SCENARIO ? 'http://gateway:8000' : 'http://api:8000');
const RUN_ID = __ENV.RUN_ID || `${SCENARIO}-kubernetes-local`;
const PRODUCT_IDS = [1, 2, 3];
const LOAD_STAGES = [
  {
    name: 'stage_1',
    target: integerEnv('STAGE_1_RATE', 20),
    duration: __ENV.STAGE_1_DURATION || '20s',
  },
  {
    name: 'stage_2',
    target: integerEnv('STAGE_2_RATE', RATE_LIMITED_SCENARIO ? 22 : 40),
    duration: __ENV.STAGE_2_DURATION || '30s',
  },
  {
    name: 'stage_3',
    target: integerEnv('STAGE_3_RATE', RATE_LIMITED_SCENARIO ? 22 : 60),
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
const requestsStarted = new Counter('requests_started');
const totalThroughput = new Counter('throughput_total');
const acceptedThroughput = new Counter('throughput_accepted_2xx');
const rejectedThroughput = new Counter('throughput_rejected_429');
const unexpectedFailures = new Counter('unexpected_failures');
const rateLimitedPercentage = new Rate('rate_limited_percentage');
const rateLimitHeadersPresent429 = new Rate('rate_limit_headers_present_429');
const responseDuration2xx = new Trend('response_duration_2xx', true);
const responseDuration429 = new Trend('response_duration_429', true);
const expectedStatuses = http.expectedStatuses({ min: 200, max: 299 }, 429);

function hasRateLimitHeaders(response) {
  const headers = {};
  for (const name in response.headers || {}) {
    headers[name.toLowerCase()] = response.headers[name];
  }
  const standardHeaders =
    headers['ratelimit-limit'] !== undefined &&
    headers['ratelimit-remaining'] !== undefined &&
    headers['ratelimit-reset'] !== undefined;
  const kongHeaders =
    headers['x-ratelimit-limit-second'] !== undefined &&
    headers['x-ratelimit-remaining-second'] !== undefined;
  return standardHeaders || kongHeaders;
}

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

  requestsStarted.add(1, metricTags);
  const response = http.post(`${BASE_URL}/checkout`, payload, {
    headers: {
      'Content-Type': 'application/json',
    },
    timeout: __ENV.REQUEST_TIMEOUT || '60s',
    responseCallback: expectedStatuses,
    tags: {
      name: 'POST /checkout',
      ...metricTags,
    },
  });

  const classification = classifyResponse(response);
  totalThroughput.add(1, metricTags);
  rateLimitedPercentage.add(classification === RATE_LIMITED_429, metricTags);

  if (classification === ACCEPTED_2XX) {
    responses2xx.add(1, metricTags);
    acceptedThroughput.add(1, metricTags);
    responseDuration2xx.add(response.timings.duration, metricTags);
  } else if (classification === RATE_LIMITED_429) {
    responses429.add(1, metricTags);
    rejectedThroughput.add(1, metricTags);
    responseDuration429.add(response.timings.duration, metricTags);
    rateLimitHeadersPresent429.add(hasRateLimitHeaders(response), metricTags);
  } else if (classification === UNEXPECTED_5XX) {
    responses5xx.add(1, metricTags);
    unexpectedFailures.add(1, metricTags);
  } else if (classification === CONNECTION_ERROR) {
    connectionErrors.add(1, metricTags);
    unexpectedFailures.add(1, metricTags);
  } else {
    unexpectedStatuses.add(1, metricTags);
    unexpectedFailures.add(1, metricTags);
  }

  if (classification === RATE_LIMITED_429) {
    check(response, {
      'status 429 protegido': (res) => res.status === 429,
      '429 possui cabecalhos de rate limiting': hasRateLimitHeaders,
    });
    return;
  }

  if (classification !== ACCEPTED_2XX) {
    return;
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
