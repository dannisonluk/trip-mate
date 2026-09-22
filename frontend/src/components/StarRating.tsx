import { Star } from "lucide-react";

import { cn } from "@/lib/utils";

export default function StarRating({
  value,
  size = "sm",
  showValue = false,
  className,
}: {
  value: number | null | undefined;
  size?: "sm" | "md" | "lg";
  showValue?: boolean;
  className?: string;
}) {
  const rating = value ?? 0;
  const px = size === "lg" ? "h-5 w-5" : size === "md" ? "h-4 w-4" : "h-3.5 w-3.5";

  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      <span className="inline-flex">
        {[1, 2, 3, 4, 5].map((i) => (
          <Star
            key={i}
            className={cn(
              px,
              i <= Math.round(rating)
                ? "fill-accent text-accent"
                : "fill-transparent text-muted-foreground/40",
            )}
          />
        ))}
      </span>
      {showValue && (
        <span className="text-xs font-semibold text-muted-foreground">
          {value != null ? value.toFixed(1) : "—"}
        </span>
      )}
    </span>
  );
}
