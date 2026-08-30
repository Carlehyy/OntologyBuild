/**
 * Vendored from beUI (github.com/starc007/ui-components @ afba7fa055dd, MIT © 2026 Saurabh Chauhan).
 * 平台适配：texts/days/fromKey 从 index 下发（按 key 而非 label 过滤兄弟日）；
 * border-border-strong → border-[var(--color-border-hover)]（tokens.css 语义令牌）。
 */

import { Check, Copy } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { Checkbox } from "./motion/checkbox";
import {
  MorphPopover,
  MorphPopoverContent,
} from "./motion/popover-morph";
import { Tooltip } from "./motion/tooltip";
import { SPRING_PRESS } from "./motion/ease";
import { IconButton } from "./icon-button";
import { type DayKey, type SchedulerTexts } from "./types";

// Copy this day's hours to other days: a morph popover with a day picker.
export function CopyMenu({
  fromKey,
  fromLabel,
  days,
  texts,
  reduce,
  onApply,
}: {
  fromKey: DayKey;
  fromLabel: string;
  days: { key: DayKey; label: string }[];
  texts: SchedulerTexts;
  reduce: boolean;
  onApply: (targets: DayKey[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [picked, setPicked] = useState<Set<DayKey>>(new Set());
  const others = days.filter((d) => d.key !== fromKey);

  const toggle = (k: DayKey) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  const apply = (targets: DayKey[]) => {
    if (!targets.length) return;
    onApply(targets);
    setOpen(false);
    setPicked(new Set());
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  return (
    <MorphPopover open={open} onOpenChange={setOpen}>
      <Tooltip content={texts.copyTimes}>
        <IconButton
          label={`${texts.copyTimes}：${fromLabel}`}
          reduce={reduce}
          expanded={open}
          onClick={() => setOpen(!open)}
        >
          <AnimatePresence mode="popLayout" initial={false}>
            {copied ? (
              <motion.span
                key="done"
                initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.5 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.5 }}
                transition={SPRING_PRESS}
                className="text-foreground"
              >
                <Check className="h-4 w-4" />
              </motion.span>
            ) : (
              <motion.span
                key="copy"
                initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.5 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.5 }}
                transition={SPRING_PRESS}
              >
                <Copy className="h-4 w-4" />
              </motion.span>
            )}
          </AnimatePresence>
        </IconButton>
      </Tooltip>

      <MorphPopoverContent align="end" className="w-52 p-2">
        <p className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
          {texts.copyTo}
        </p>
        <div className="flex flex-col">
          {others.map((d) => (
            <Checkbox
              key={d.key}
              checked={picked.has(d.key)}
              onCheckedChange={() => toggle(d.key)}
              label={d.label}
              className="w-full flex-row-reverse justify-between rounded-lg px-2 py-1.5 transition-colors hover:bg-muted [&_button]:size-4 [&_button]:rounded-[5px] [&_button]:border [&_button[data-state=unchecked]]:border-[var(--color-border-hover)]"
            />
          ))}
        </div>
        <div className="mt-1 flex items-center gap-2 border-t border-border px-1 pt-2">
          <button
            type="button"
            onClick={() => apply(others.map((d) => d.key))}
            className="flex-1 rounded-lg px-2 py-1.5 text-xs font-medium text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:bg-muted"
          >
            {texts.everyDay}
          </button>
          <button
            type="button"
            onClick={() => apply([...picked])}
            disabled={picked.size === 0}
            className="flex-1 rounded-lg bg-primary px-2 py-1.5 text-xs font-semibold text-primary-foreground outline-none transition-opacity hover:opacity-90 focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40"
          >
            {texts.apply}
          </button>
        </div>
      </MorphPopoverContent>
    </MorphPopover>
  );
}
