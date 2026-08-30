/**
 * Vendored from beUI (github.com/starc007/ui-components @ afba7fa055dd, MIT © 2026 Saurabh Chauhan).
 * 平台适配：仅导入路径（motion 原语随组件目录内聚）。
 */

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./motion/select";
import type { TimeOption } from "./types";

// Time field: the library Select, with the option list capped so the panel
// measures a small height and scrolls instead of unfolding all 48 options.
export function TimeSelect({
  value,
  onChange,
  open,
  onOpenChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  options: TimeOption[];
}) {
  return (
    <Select
      value={value}
      onValueChange={onChange}
      open={open}
      onOpenChange={onOpenChange}
      className="w-full"
    >
      <SelectTrigger className="tabular-nums">
        <SelectValue className="whitespace-nowrap" />
      </SelectTrigger>
      <SelectContent>
        <div className="max-h-56 overflow-y-auto overscroll-contain">
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value} className="tabular-nums">
              {o.label}
            </SelectItem>
          ))}
        </div>
      </SelectContent>
    </Select>
  );
}
