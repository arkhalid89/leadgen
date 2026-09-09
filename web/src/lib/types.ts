export type Source = "gmaps" | "websearch";
export type EnrichMode = "off" | "standard";
export type JobStatus = "queued" | "running" | "completed" | "stopped" | "failed";
export type Quality = "strong" | "medium" | "weak";

export interface User {
  id: number;
  email: string;
  full_name: string;
  is_active: boolean;
  license_key: string;
  created_at?: string;
}

export interface Job {
  id: string;
  user_id: number;
  source: Source;
  keyword: string;
  location: string;
  max_leads: number;
  enrich_mode: EnrichMode;
  status: JobStatus;
  stage: string;
  progress: number;
  message: string;
  total_found: number;
  enriched_count: number;
  error: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  running?: boolean;
}

export interface Lead {
  id: number;
  job_id: string;
  uid: string;
  source: Source;
  business_name: string;
  owner_name: string;
  email: string;
  phone: string;
  /** Every address/number found. `email`/`phone` are the primary ones. */
  emails: string[];
  phones: string[];
  /** 'website' = read off their site, 'ai' = model suggestion. */
  email_source: string;
  status?: string;
  favourite?: number;
  tags?: string[];
  /** 'verified' or 'unverified'. */
  email_status: string;
  website: string;
  address: string;
  rating: string;
  reviews: string;
  category: string;
  latitude: string;
  longitude: string;
  place_id: string;
  facebook: string;
  instagram: string;
  twitter: string;
  linkedin: string;
  youtube: string;
  summary: string;
  quality: Quality;
  enriched: number;
  keyword: string;
  location: string;
  created_at: string;
}

export interface JobEvent {
  id: number;
  job_id: string;
  level: string;
  stage: string;
  progress: number;
  message: string;
  created_at: string;
}

export interface DashboardStats {
  leads: {
    total: number;
    with_email: number;
    with_phone: number;
    enriched: number;
    email_rate: number;
    strong: number;
    medium: number;
    weak: number;
  };
  jobs: { total: number; completed: number; active: number; failed: number };
  email_templates: number;
}

export interface ServerConfig {
  /** Emails come from the businesses' own websites, not from a model. */
  email_source: string;
  email_concurrency: number;
  email_max_pages: number;
  smtp_verify_enabled: boolean;
  /** Optional, and only used to draft outreach copy. */
  gemini_configured: boolean;
  gemini_model: string;
  search_backend: string;
  gmaps_detail_workers: number;
  max_active_jobs: number;
}

export interface EmailTemplate {
  id: number;
  lead_id: number | null;
  business_name: string;
  email: string;
  subject: string;
  body: string;
  keyword: string;
  location: string;
  created_at: string;
}

export interface LeadStats {
  total: number;
  with_email: number;
  with_phone: number;
  with_website: number;
  enriched: number;
  multi_email: number;
  multi_phone: number;
  by_quality: Record<string, number>;
  by_source: Record<string, number>;
}

export const SOURCE_LABELS: Record<Source, string> = {
  gmaps: "Google Maps",
  websearch: "Web Search",
};

export const LEAD_STATUSES = [
  "new",
  "contacted",
  "replied",
  "qualified",
  "customer",
  "rejected",
] as const;

export interface LeadList {
  id: number;
  name: string;
  description: string;
  colour: string;
  lead_count: number;
  created_at: string;
}

export interface LeadNote {
  id: number;
  kind: string;
  body: string;
  created_at: string;
}

export interface LeadDetail extends Lead {
  tags: string[];
  notes: LeadNote[];
  lists: { id: number; name: string; colour: string }[];
  drafts: { id: number; subject: string; created_at: string }[];
}

export interface TagCount {
  tag: string;
  count: number;
}

export interface SuppressionEntry {
  id: number;
  value: string;
  kind: string;
  reason: string;
  created_at: string;
}

export interface SavedSearch {
  id: number;
  name: string;
  source: Source;
  keyword: string;
  location: string;
  params: Record<string, unknown>;
  run_count: number;
  last_run: string | null;
  created_at: string;
}
