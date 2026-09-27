"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Candle } from "@/lib/api";

export function CandleChart({ candles }: { candles: Candle[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: "#a1a1aa",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "#27272a33" },
        horzLines: { color: "#27272a33" },
      },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#27272a" },
      rightPriceScale: { borderColor: "#27272a" },
      crosshair: { mode: 0 },
    });
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || candles.length === 0) return;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#34d399",
      downColor: "#f87171",
      borderVisible: false,
      wickUpColor: "#34d399",
      wickDownColor: "#f87171",
    });
    const volSeries = chart.addSeries(HistogramSeries, { priceScaleId: "vol" });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

    candleSeries.setData(
      candles.map((c) => ({
        time: Math.floor(c.timestamp) as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    );
    volSeries.setData(
      candles.map((c) => ({
        time: Math.floor(c.timestamp) as UTCTimestamp,
        value: c.volume,
        color: c.close >= c.open ? "#34d39933" : "#f8717133",
      }))
    );
    chart.timeScale().fitContent();

    return () => {
      chart.removeSeries(candleSeries);
      chart.removeSeries(volSeries);
    };
  }, [candles]);

  return <div ref={containerRef} className="h-[380px] w-full" />;
}
