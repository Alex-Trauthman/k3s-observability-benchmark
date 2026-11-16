import http from 'k6/http';
import { check } from 'k6';

export const options = {
  scenarios: {
    constant_request_rate: {
      executor: 'constant-arrival-rate',
      rate: parseInt(__ENV.TEST_RPS || '50'),
      timeUnit: '1s',
      duration: __ENV.TEST_DURATION || '30s',
      preAllocatedVUs: 40,
      maxVUs: 250,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.50'],
  },
};

const TARGET_HOST = __ENV.TARGET_HOST || '10.158.0.3:30080';

export default function () {
  const url = `http://${TARGET_HOST}/work`;
  const res = http.get(url, { timeout: '4s' });
  check(res, {
    'status is 200 or 500': (r) => r.status === 200 || r.status === 500,
  });
}
