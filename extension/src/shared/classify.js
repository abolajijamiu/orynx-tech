/**
 * Platform classification and lead scoring.
 *
 * A known domain gets its category and purchase signal from the registry. An
 * unknown one — the site you found this morning — is classified from what the
 * page itself says, so the tool is useful immediately rather than after someone
 * adds a config entry.
 */

import { registrableDomain } from "./normalize.js";

let REGISTRY = null;
export function loadRegistry(json) {
  REGISTRY = json;
}

// Words that reliably distinguish the business models, checked against page text.
const SIGNAL_RULES = [
  { signal: "vanity_published", category: "publisher_vanity", weight: 0.9,
    any: ["publishing package", "author contribution", "contributory", "publishing costs",
          "our publishing packages", "author investment", "we publish your book"] },
  { signal: "paid_review", category: "review_paid", weight: 0.85,
    any: ["submit your book for review", "review package", "paid review", "review fee",
          "expedited review", "submit for review", "indie review"] },
  { signal: "hybrid_published", category: "publisher_hybrid", weight: 0.8,
    any: ["hybrid publish", "partner publish", "co-publish", "shared investment"] },
  { signal: "paid_promotion", category: "promo_service", weight: 0.78,
    any: ["book promotion", "promote your book", "advertise your book", "featured listing",
          "newsletter promotion", "book marketing services"] },
  { signal: "self_published", category: "selfpub_platform", weight: 0.72,
    any: ["self-publish", "self publishing", "upload your book", "print on demand",
          "distribute your book"] },
  { signal: "seeking_publication", category: "education", weight: 0.5,
    any: ["submission guidelines", "open for submissions", "query letter", "we accept submissions"] },
  { signal: "indie_published", category: "publisher_indie", weight: 0.55,
    any: ["our authors", "our catalogue", "our catalog", "our titles", "forthcoming titles"] },
];

export function classifyPage(hostname, pageText) {
  const domain = registrableDomain(hostname);
  const known = REGISTRY && (REGISTRY[hostname.replace(/^www\./, "")] || REGISTRY[domain]);
  if (known) {
    return {
      platformId: known.id,
      company: known.name,
      category: known.category,
      signal: known.signal,
      country: known.country,
      owner: known.owner,
      services: known.services,
      weight: known.weight,
      known: true,
    };
  }

  const text = (pageText || "").toLowerCase().slice(0, 200000);
  for (const rule of SIGNAL_RULES) {
    if (rule.any.some((phrase) => text.includes(phrase))) {
      return {
        platformId: domain,
        company: null,
        category: rule.category,
        signal: rule.signal,
        country: guessCountry(hostname),
        owner: null,
        services: [],
        weight: rule.weight,
        known: false,
      };
    }
  }
  return {
    platformId: domain, company: null, category: "unknown", signal: "community_listed",
    country: guessCountry(hostname), owner: null, services: [], weight: 0.4, known: false,
  };
}

const TLD_COUNTRY = {
  uk: "UK", ca: "CA", au: "AU", nz: "NZ", ie: "IE", in: "IN", ng: "NG", za: "ZA",
  de: "DE", fr: "FR", es: "ES", it: "IT", nl: "NL", se: "SE", no: "NO", dk: "DK",
};

function guessCountry(hostname) {
  const tld = String(hostname).split(".").pop();
  return TLD_COUNTRY[tld] || "";
}

// What each purchase signal implies about the pitch. These are starting drafts
// meant to be edited, not sent as written.
const PITCH_BY_SIGNAL = {
  vanity_published: "Paid a publisher up front — already spends on their book. Lead with results the package did not deliver: reviews, visibility, sales.",
  hybrid_published: "Co-invested with a press, so budget and commitment are proven. Offer what the press does not cover.",
  paid_review: "Bought a review, so is actively marketing. Offer to turn that review into reach.",
  paid_promotion: "Already buying promotion. Position against their current spend on measurable outcomes.",
  self_published: "Doing it themselves. Offer the piece they are least equipped for — usually design or launch marketing.",
  indie_published: "Has a small press behind them. Offer what a small press cannot resource: publicity, ads, audio.",
  trade_published: "Traditionally published — has a team already. Low priority unless targeting backlist or rights.",
  seeking_publication: "Still looking for a home. Editing, submission strategy and manuscript assessment fit best.",
  community_listed: "Weak signal from this page alone. Qualify before spending effort.",
};

export function pitchFor(signal, services) {
  const base = PITCH_BY_SIGNAL[signal] || PITCH_BY_SIGNAL.community_listed;
  if (services && services.length) {
    return `${base} Platform sells: ${services.join(", ")}.`;
  }
  return base;
}

/**
 * Priority 0-100. Recency and purchase signal dominate, because a recent book
 * from someone who already pays for services is the whole target.
 */
export function scoreLead(record, classification, today = new Date()) {
  const reasons = [];
  let score = 0;

  const weight = classification.weight ?? 0.4;
  score += weight * 40;
  reasons.push(`platform signal ${classification.signal} (${Math.round(weight * 40)})`);

  const year = record.publishedYear;
  if (year) {
    const age = today.getUTCFullYear() - year;
    const recency = age <= 0 ? 1 : Math.max(0, 1 - age / 4);
    score += recency * 30;
    if (recency > 0) reasons.push(`published ${year} (${Math.round(recency * 30)})`);
  } else {
    score += 8;
  }

  const channels = countChannels(record);
  score += Math.min(1, channels / 3) * 20;
  if (channels) reasons.push(`${channels} contact channel(s) (${Math.round(Math.min(1, channels / 3) * 20)})`);

  // Few reviews on a recent book means visibility is the unmet need.
  const reviews = record.reviewsCount;
  if (reviews !== null && reviews !== undefined) {
    const gap = Math.max(0, 1 - reviews / 50);
    score += gap * 10;
    if (gap > 0.2) reasons.push(`only ${reviews} review(s) (${Math.round(gap * 10)})`);
  }

  const rounded = Math.round(Math.min(100, score) * 10) / 10;
  return { score: rounded, tier: tierFor(rounded), reasons };
}

export function countChannels(record) {
  let count = 0;
  if (record.email) count += 1;
  if (record.phone) count += 1;
  if (record.whatsapp) count += 1;
  if (record.linkedin) count += 1;
  if (record.instagram) count += 1;
  return count;
}

export function tierFor(score) {
  if (score >= 75) return "A";
  if (score >= 55) return "B";
  if (score >= 35) return "C";
  return "D";
}
