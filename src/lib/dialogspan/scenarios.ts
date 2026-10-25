export type Scenario = {
  id: string;
  title: string;
  kicker: string;
  situation: string;
  shows: string;
  agents: { name: string; turns: string[] }[];
};

export const scenarios: Scenario[] = [
  {
    id: "order-4821",
    title: "Order 4821, Pune hub",
    kicker: "Agreement, plus a turn left out",
    situation: "Support and logistics describe the same shipment. Billing mentions the same order and talks about the invoice.",
    shows: "A shared order id is not treated as proof that every turn is the same claim.",
    agents: [
      {
        name: "Aria · Support",
        turns: [
          "Order 4821 shipped from the Pune hub. The carrier ETA is Friday 18 October.",
        ],
      },
      {
        name: "Bram · Logistics",
        turns: [
          "Tracking for order 4821 shows departure from the Pune hub. Delivery is scheduled Friday, 18 October.",
        ],
      },
      {
        name: "Cleo · Billing",
        turns: ["Order 4821 has an open invoice of ₹4,200. Payment is still due."],
      },
    ],
  },
  {
    id: "refund-window",
    title: "Refund window",
    kicker: "Disagreement, with one agreeing pair",
    situation: "Two agents state a 14-day window. A third says refunds stop after 7 days.",
    shows: "Partial agreement does not erase a numeric and negation conflict. The span stays a contradiction.",
    agents: [
      {
        name: "North · Policy",
        turns: ["Refunds are available within 14 days of delivery for unopened items."],
      },
      {
        name: "South · Desk",
        turns: [
          "This order is outside the refund window. Refunds are not available after 7 days.",
        ],
      },
      {
        name: "East · Catalog",
        turns: ["The published policy allows returns for 14 days if the item is unopened."],
      },
    ],
  },
  {
    id: "clinic-thursday",
    title: "Thursday at the clinic",
    kicker: "Not enough to decide",
    situation: "Two agents mention Thursday and different times. A third only gives arrival instructions.",
    shows: "A shared weekday and a time difference, with no booking id, stays inconclusive. The instruction turn is not pulled in.",
    agents: [
      {
        name: "Mira · Front desk",
        turns: ["Dr. Shah can see you Thursday morning."],
      },
      {
        name: "Dev · Scheduler",
        turns: ["The clinic on FC Road has a slot after 4 pm on Thursday."],
      },
      {
        name: "Lila · Intake",
        turns: ["Please bring your previous prescription and arrive 15 minutes early."],
      },
    ],
  },
  {
    id: "payments-incident",
    title: "Payments error rate",
    kicker: "Same figure, then a conflicting figure",
    situation: "Two agents report 4.2% at 02:14 UTC. A third says the rate is 0.3% and no incident is open.",
    shows: "Matching measurements correlate. A different percent on the same metric is a contradiction, not a blend.",
    agents: [
      {
        name: "Sentinel",
        turns: [
          "API error rate crossed 4.2% at 02:14 UTC.",
          "Payments is the source of the 4.2% error rate.",
        ],
      },
      {
        name: "Relay",
        turns: ["Payments error rate hit 4.2 percent at 02:14 UTC. Checkout is degraded."],
      },
      {
        name: "Ledger",
        turns: ["Payments error rate is 0.3% and within the normal band. No incident is open."],
      },
    ],
  },
  {
    id: "booking-hk204",
    title: "Booking HK-204",
    kicker: "Four agents, one disputed rate",
    situation:
      "Front desk and reservations describe the same room. Reservations also states ₹6,200. Billing states ₹8,400. Dining talks about the same guest and a different plan.",
    shows: "A rate conflict keeps the linked room turns in a contradiction span. Dinner is not joined just because the guest name matches.",
    agents: [
      {
        name: "Neel · Front desk",
        turns: [
          "Guest Isha Kapoor, booking HK-204, checks in on 12 March. King room, non-smoking.",
        ],
      },
      {
        name: "Rhea · Reservations",
        turns: [
          "Booking HK-204 for Isha Kapoor is a king room, non-smoking, arrival 12 March. The prepaid rate is ₹6,200.",
        ],
      },
      {
        name: "Anya · Billing",
        turns: ["Booking HK-204 was charged the folio rate of ₹8,400."],
      },
      {
        name: "Om · Dining",
        turns: ["Isha Kapoor asked for dinner at 21:30 on 12 March, a table for two."],
      },
    ],
  },
];

export function scenarioById(id: string) {
  return scenarios.find((scenario) => scenario.id === id) ?? scenarios[0];
}
