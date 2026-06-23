import { useEffect, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/apiClient";

interface Suggestion {
  place_id: string;
  description: string;
  primary: string;
  secondary?: string | null;
}

/**
 * Nairobi-biased address input backed by the server-side Places proxy
 * (`/api/fleet/places/suggest` + `/places/details`). Picking a suggestion
 * captures exact coordinates; free-typed text still works (the backend geocodes
 * it on submit), so this is a pure enhancement and never blocks entry.
 */
export function AddressAutocomplete({
  value,
  placeholder,
  testId,
  onChange,
  onResolve,
}: {
  value: string;
  placeholder?: string;
  testId?: string;
  /** Free-text edits — coordinates are no longer known. */
  onChange: (address: string) => void;
  /** A suggestion was picked — address plus resolved coordinates. */
  onResolve: (address: string, lat: number, lng: number) => void;
}) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const skipNextLookup = useRef(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (skipNextLookup.current) {
      skipNextLookup.current = false;
      return;
    }
    const q = value.trim();
    if (q.length < 3) {
      setSuggestions([]);
      setOpen(false);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const res = await api.get("/api/fleet/places/suggest", { q });
        const list: Suggestion[] = res.suggestions ?? [];
        setSuggestions(list);
        setOpen(list.length > 0);
        setActive(-1);
      } catch {
        /* autocomplete is best-effort */
      }
    }, 250);
    return () => clearTimeout(handle);
  }, [value]);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const choose = async (s: Suggestion) => {
    skipNextLookup.current = true;
    onChange(s.description);
    setOpen(false);
    setSuggestions([]);
    try {
      const res = await api.get("/api/fleet/places/details", { place_id: s.place_id });
      if (res.found) onResolve(res.label || s.description, res.lat, res.lng);
    } catch {
      /* fall back to free text — backend geocodes on submit */
    }
  };

  return (
    <div className="relative flex-1" ref={boxRef}>
      <Input
        placeholder={placeholder ?? "Address"}
        value={value}
        autoComplete="off"
        data-testid={testId}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => suggestions.length > 0 && setOpen(true)}
        onKeyDown={(e) => {
          if (!open) return;
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, suggestions.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter" && active >= 0) {
            e.preventDefault();
            choose(suggestions[active]);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />
      {open && suggestions.length > 0 && (
        <ul
          className="absolute z-50 mt-1 max-h-60 w-full overflow-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
          data-testid="address-suggestions"
        >
          {suggestions.map((s, i) => (
            <li key={s.place_id}>
              <button
                type="button"
                className={`w-full rounded px-2 py-1.5 text-left text-sm hover:bg-accent ${
                  i === active ? "bg-accent" : ""
                }`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(s)}
              >
                <span className="font-medium">{s.primary}</span>
                {s.secondary && <span className="block text-xs text-muted-foreground">{s.secondary}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
