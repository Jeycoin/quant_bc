import { Placeholder } from "@/components/placeholder";

export default function TimelinePage() {
  return (
    <Placeholder
      title="AI Decision Timeline"
      phase="Phase 5"
      items={[
        "MARKET_SCAN → ANALYSIS → STRATEGY_SELECTION → RISK_CHECK → EXECUTOR_CREATED → ORDER → FILL → POSITION → REVIEW",
        "Structured decision summaries only — no chain-of-thought",
        "Timestamp / symbol / event type / summary / result",
      ]}
    />
  );
}
