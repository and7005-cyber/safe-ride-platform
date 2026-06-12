import type { LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type Variant = "default" | "success" | "warning" | "destructive";

const iconStyles: Record<Variant, string> = {
  default: "bg-secondary text-secondary-foreground",
  success: "bg-success/10 text-success",
  warning: "bg-warning/15 text-warning",
  destructive: "bg-destructive/10 text-destructive",
};

export function StatCard({
  label,
  value,
  subtitle,
  icon: Icon,
  variant = "default",
}: {
  label: string;
  value: number | string;
  subtitle?: string;
  icon: LucideIcon;
  variant?: Variant;
}) {
  return (
    <Card>
      <CardContent className="flex items-start justify-between gap-4 p-5">
        <div className="space-y-1">
          <p className="text-sm font-medium text-muted-foreground">{label}</p>
          <p className="font-heading text-3xl font-bold">{value}</p>
          {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
        </div>
        <div className={cn("flex h-10 w-10 items-center justify-center rounded-lg", iconStyles[variant])}>
          <Icon className="h-5 w-5" />
        </div>
      </CardContent>
    </Card>
  );
}
