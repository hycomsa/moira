import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "ghost" | "subtle" | "danger" | "success";
type Size = "sm" | "md";

// Shared button — one consistent look across the cockpit.
export function Button({ variant = "subtle", size = "md", className = "", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }) {
  return <button className={`ui-btn ui-btn-${variant} ui-btn-${size} ${className}`} {...rest} />;
}
