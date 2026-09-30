// @vitest-environment node
// Without a DOM, DOMPurify cannot sanitize. renderMarkdown must FAIL CLOSED there (escape), never pass
// executable markup through.
import { describe, expect, it } from 'vitest';
import { renderMarkdown } from '$lib/utils/markdown';

describe('renderMarkdown without a DOM', () => {
  it('escapes instead of passing markup through', () => {
    const html = renderMarkdown('hi <script>alert(1)</script> <img src=x onerror=alert(1)>');
    expect(html).not.toContain('<script');
    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;script&gt;');
  });
});
