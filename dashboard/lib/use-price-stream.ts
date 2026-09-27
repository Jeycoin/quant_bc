"use client";

import { useEffect, useState } from "react";
import { API_BASE_URL } from "@/lib/api";

export type PriceTick = {
  ts: number;
  prices?: Record<string, number>;
  error?: string;
};

/** SSE price stream with graceful degradation: returns null when the
 * stream is unavailable — callers keep their React Query polling data. */
export function usePriceStream(): PriceTick | null {
  const [tick, setTick] = useState<PriceTick | null>(null);

  useEffect(() => {
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout>;
    let stopped = false;

    const connect = () => {
      es = new EventSource(`${API_BASE_URL}/api/stream`);
      es.onmessage = (ev) => {
        try {
          setTick(JSON.parse(ev.data));
        } catch {
          /* malformed frame — ignore */
        }
      };
      es.onerror = () => {
        es?.close();
        if (!stopped) retry = setTimeout(connect, 10_000);
      };
    };
    connect();

    return () => {
      stopped = true;
      clearTimeout(retry);
      es?.close();
    };
  }, []);

  return tick;
}
