import type {Matrix} from './selection';

export type Strategy = {
  algorithm: 'V3' | 'flat' | 'pindou' | 'pindou_refine';
  fit: 'pad' | 'crop';
  subject_scale: number;
};

export type Version = {
  id: string;
  project: string;
  kind: string;
  parent: string | null;
  source?: string;
  role?: 'reference' | 'artwork';
  matrix?: Matrix;
  strategy?: Strategy;
  pending_artwork_edit?: boolean;
};

export type Color = {id: string; name: string; hex: string};

type Status = 'queued' | 'running' | 'needs_input' | 'ready' | 'failed';
export type Review = {
  pattern_version_id: string;
  issues: {code: string; side?: string; count?: number}[];
};

export type Run = Partial<Review> & {
  id: string;
  status: Status;
  stage: string;
  question?: string;
  question_id?: string;
  error?: {message: string};
};

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch('/api' + path, body instanceof FormData
    ? {method: 'POST', body}
    : body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {});
  if (!response.ok) {
    const failure = await response.json();
    throw new Error(typeof failure.detail === 'string' ? failure.detail : '请求失败，请检查输入后重试。');
  }
  return response.json();
}
