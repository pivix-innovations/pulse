export interface Membership { tenant_id: string; tenant_name: string; role: "admin" | "member" | string }
export interface Me {
  id: string; email: string; full_name?: string | null;
  is_platform_admin: boolean; memberships: Membership[];
}
export interface Provider {
  id: string; label: string; channel: string; provider_key?: string; is_active?: boolean;
  logo_url?: string; catalog?: { logo_url?: string; name?: string } | null;
  [k: string]: any;
}
