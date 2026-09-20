export const ACCEPTED_2XX = 'accepted_2xx';
export const RATE_LIMITED_429 = 'rate_limited_429';
export const UNEXPECTED_5XX = 'unexpected_5xx';
export const CONNECTION_ERROR = 'connection_error';
export const UNEXPECTED_STATUS = 'unexpected_status';

export function classifyResponse(response) {
  if (response !== null && response !== undefined) {
    if (response.status >= 200 && response.status < 300) {
      return ACCEPTED_2XX;
    }
    if (response.status === 429) {
      return RATE_LIMITED_429;
    }
    if (response.status >= 500 && response.status < 600) {
      return UNEXPECTED_5XX;
    }
  }

  if (
    response === null ||
    response === undefined ||
    response.status === 0 ||
    response.error ||
    response.error_code
  ) {
    return CONNECTION_ERROR;
  }

  return UNEXPECTED_STATUS;
}
