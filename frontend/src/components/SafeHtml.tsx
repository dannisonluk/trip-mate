"use client";

import DOMPurify from "isomorphic-dompurify";
import { useMemo } from "react";

/**
 * Renders user-authored rich text safely (§5.2 XSS protection).
 * All HTML is sanitised through DOMPurify with a strict allow-list.
 */
export default function SafeHtml({ html, className }: { html: string; className?: string }) {
  const clean = useMemo(
    () =>
      DOMPurify.sanitize(html, {
        ALLOWED_TAGS: ["b", "i", "em", "strong", "a", "br", "p", "ul", "ol", "li", "code"],
        ALLOWED_ATTR: ["href", "target", "rel"],
        ALLOW_DATA_ATTR: false,
      }),
    [html],
  );

  return <div className={className} dangerouslySetInnerHTML={{ __html: clean }} />;
}
