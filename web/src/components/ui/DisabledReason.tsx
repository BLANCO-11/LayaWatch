/* Disabled control wrapper (plan task 1, design-language 4.23): renders the
 * real `disabled` attribute on the wrapped control plus a Tooltip naming the
 * reason. Pass `null`/`undefined` as the reason and the control renders
 * enabled and unwrapped, so callers compute the reason once. */
"use client";

import { cloneElement } from "react";
import Tooltip from "./Tooltip";

export default function DisabledReason({
  reason,
  children,
}: {
  reason?: string | null;
  children: React.ReactElement<{ disabled?: boolean }>;
}) {
  if (!reason) return <>{children}</>;
  const disabled = cloneElement(children, { disabled: true });
  return <Tooltip text={reason}>{disabled}</Tooltip>;
}
