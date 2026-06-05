import type { CSSProperties, SelectHTMLAttributes, ReactNode } from "react";

// Native <select> with appearance:none + a custom chevron and correct vertical
// centering (fixes the clipped/mis-aligned dropdowns). className/style size the
// wrapper; all other props (value, onChange, title, disabled…) go to the select.
export function Select({ className = "", style, children, ...rest }:
  SelectHTMLAttributes<HTMLSelectElement> & { style?: CSSProperties; children?: ReactNode }) {
  return (
    <div className={"ui-select-wrap " + className} style={style}>
      <select className="ui-select" {...rest}>{children}</select>
      <span className="ui-select-chev" aria-hidden>▾</span>
    </div>
  );
}
