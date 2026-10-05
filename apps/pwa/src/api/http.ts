export class HttpError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/** Throws `Error(errorPrefix + status)` on non-2xx; `errorPrefix` carries its own separator. */
export async function readJsonOrThrow<T>(res: Response, errorPrefix: string): Promise<T> {
  if (!res.ok) {
    throw new Error(`${errorPrefix}${res.status}`);
  }
  return (await res.json()) as T;
}

export async function getJson<T>(url: string, errorPrefix: string): Promise<T> {
  return readJsonOrThrow<T>(await fetch(url), errorPrefix);
}
