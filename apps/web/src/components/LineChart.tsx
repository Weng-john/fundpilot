import { useEffect, useRef } from "react";
import { ColorType, LineSeries, createChart, type UTCTimestamp } from "lightweight-charts";

export type Point = { t: string; v: number };

/** Minimal time-series line chart (lightweight-charts). */
export function LineChart({ series, height = 260, precision = 4 }: { series: { name: string; color: string; points: Point[] }[]; height?: number; precision?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      height,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#9aa4b2" },
      grid: { vertLines: { color: "#1f2733" }, horzLines: { color: "#1f2733" } },
      timeScale: { timeVisible: true },
      rightPriceScale: { borderVisible: false },
      autoSize: true,
    });
    for (const s of series) {
      const line = chart.addSeries(LineSeries, {
        color: s.color,
        lineWidth: 2,
        title: s.name,
        priceFormat: { type: "price", precision, minMove: 10 ** -precision },
      });
      const seen = new Set<number>();
      const data = s.points
        .map((p) => ({ time: Math.floor(Date.parse(p.t) / 1000) as UTCTimestamp, value: p.v }))
        .filter((p) => (seen.has(p.time) ? false : (seen.add(p.time), true)))
        .sort((a, b) => a.time - b.time);
      line.setData(data);
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, height, precision]);
  return <div ref={ref} className="chart" />;
}
