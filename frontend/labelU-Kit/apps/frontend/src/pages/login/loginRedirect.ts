export function normalizeRedirect(raw: string | null) {
  if (!raw || raw.charAt(0) !== '/' || /^\/login/i.test(raw)) {
    return '/workspace';
  }

  return raw;
}
