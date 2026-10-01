import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/**
 * SvelteKit config — Phase 1 scaffold (Plan 01-03).
 *
 * Notes:
 * - adapter-node: single-LXC deployment supervised by systemd.
 * - $lib alias: shadcn-svelte components and stores import via `$lib/*`.
 * - kit.csrf.checkOrigin = false (via empty trustedOrigins): Caddy is the
 *   trust boundary and the API enforces a double-submit CSRF cookie pattern
 *   (decision D-13). The API-side csrf_protect dependency is the
 *   authoritative check; SvelteKit's built-in same-origin check is left at
 *   defaults so it remains active for SvelteKit-handled form POSTs while the
 *   API handles its own.
 *
 * @type {import('@sveltejs/kit').Config}
 */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter(),
    // Content-Security-Policy is emitted by SvelteKit itself (mode 'auto': a hash for
    // prerendered pages, a per-request nonce for SSR pages) so `script-src` needs NO
    // 'unsafe-inline' (F-12). Caddy no longer sets a CSP of its own. style-src keeps
    // 'unsafe-inline': Svelte transitions and bits-ui set inline style attributes.
    csp: {
      mode: 'auto',
      directives: {
        'default-src': ['self'],
        'script-src': ['self'],
        'style-src': ['self', 'unsafe-inline'],
        'img-src': ['self', 'data:', 'https:'],
        'connect-src': ['self'],
        'font-src': ['self', 'data:'],
        'frame-ancestors': ['self'],
        'base-uri': ['self'],
        'form-action': ['self'],
        'object-src': ['none']
      }
    },
    alias: {
      $lib: './src/lib'
    }
  }
};

export default config;
