import type { ReactNode } from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface FilterConfig {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}

export function ListToolbar({
  search,
  onSearch,
  placeholder = "Search…",
  filters = [],
  actions,
}: {
  search: string;
  onSearch: (v: string) => void;
  placeholder?: string;
  filters?: FilterConfig[];
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="relative min-w-48 flex-1">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          className="pl-9"
          placeholder={placeholder}
          value={search}
          onChange={(e) => onSearch(e.target.value)}
        />
      </div>
      {filters.map((f, i) => (
        <Select key={i} value={f.value} onValueChange={f.onChange}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {f.options.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ))}
      {actions && <div className="ml-auto flex gap-2">{actions}</div>}
    </div>
  );
}
