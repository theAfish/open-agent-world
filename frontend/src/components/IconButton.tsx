import type { ButtonHTMLAttributes } from "react";
import type { LucideIcon } from "lucide-react";

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "aria-label"> {
  icon: LucideIcon;
  label: string;
  size?: "xs" | "sm" | "md";
  quiet?: boolean;
  danger?: boolean;
}

const iconSizes = { xs: 11, sm: 12, md: 15 };

/** Shared sizing, accessible labels, and event isolation for icon-only actions. */
export function IconButton({
  icon: Icon, label, size = "md", quiet = false, danger = false,
  className = "", title = label, type = "button", onClick, ...props
}: IconButtonProps) {
  return (
    <button {...props} type={type} aria-label={label} title={title}
      className={`icon-button icon-button--${size} nodrag nopan ${quiet ? "icon-button--quiet" : ""} ${danger ? "icon-button--danger" : ""} ${className}`}
      onClick={(event) => {
        event.stopPropagation();
        onClick?.(event);
      }}>
      <Icon size={iconSizes[size]} strokeWidth={1.7} aria-hidden="true" focusable="false" />
    </button>
  );
}
