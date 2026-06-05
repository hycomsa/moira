import type { ButtonHTMLAttributes } from "react";

export function IconButton({ className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={"ui-iconbtn " + className} {...rest} />;
}
