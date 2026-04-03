/**
 * Base URL for API calls (no trailing slash).
 * Empty string = same origin as the page (local dev + FastAPI, or proxied API).
 * For split hosting, set Amplify env BETTOR_API_URL and inject in amplify.yml preBuild.
 */
export const API_BASE = "";
