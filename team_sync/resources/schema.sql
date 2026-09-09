-- agent-team-sync: leases + shared cursors + team key. Idempotent; apply with `team-sync db install`.
-- Access: PostgREST RPC with the anon key plus a team passphrase (p_key). All three tables have RLS on with no policies,
-- so only these security-definer functions can touch them, and each checks the passphrase first. Members never get the service key.

create table if not exists public.team_config (
  key   text primary key,
  value text not null,
  updated_at timestamptz not null default now()
);
alter table public.team_config enable row level security;

create table if not exists public.agent_leases (
  resource    text primary key,
  holder      text not null,
  note        text,
  acquired_at timestamptz not null default now(),
  renewed_at  timestamptz,
  expires_at  timestamptz not null
);
alter table public.agent_leases enable row level security;

create table if not exists public.collect_cursors (
  name       text primary key,
  value      bigint not null,
  updated_by text,
  updated_at timestamptz not null default now()
);
alter table public.collect_cursors enable row level security;

create or replace function public.team_ok(p_key text) returns boolean
language sql security definer stable set search_path = public as $$
  select exists (select 1 from public.team_config
                 where key = 'team_key' and length(coalesce(p_key, '')) >= 16 and value = p_key);
$$;
revoke all on function public.team_ok(text) from public, anon, authenticated;

create or replace function public.lease_acquire(p_key text, p_resource text, p_holder text, p_ttl_sec int default 900, p_note text default null)
returns jsonb language plpgsql security definer set search_path = public as $$
declare r public.agent_leases;
begin
  if not public.team_ok(p_key) then raise exception 'bad team key'; end if;
  if p_ttl_sec < 30 or p_ttl_sec > 86400 then raise exception 'ttl must be 30..86400 s'; end if;
  insert into public.agent_leases (resource, holder, note, expires_at)
  values (p_resource, p_holder, p_note, now() + make_interval(secs => p_ttl_sec))
  on conflict (resource) do update
    set holder = excluded.holder, note = excluded.note, acquired_at = now(), renewed_at = null, expires_at = excluded.expires_at
    where public.agent_leases.expires_at < now() or public.agent_leases.holder = excluded.holder
  returning * into r;
  if r.resource is null then
    select * into r from public.agent_leases where resource = p_resource;
    return jsonb_build_object('ok', false, 'holder', r.holder, 'note', r.note, 'acquired_at', r.acquired_at, 'expires_at', r.expires_at);
  end if;
  return jsonb_build_object('ok', true, 'holder', r.holder, 'expires_at', r.expires_at);
end $$;

create or replace function public.lease_renew(p_key text, p_resource text, p_holder text, p_ttl_sec int default 900)
returns jsonb language plpgsql security definer set search_path = public as $$
declare r public.agent_leases;
begin
  if not public.team_ok(p_key) then raise exception 'bad team key'; end if;
  update public.agent_leases set renewed_at = now(), expires_at = now() + make_interval(secs => p_ttl_sec)
   where resource = p_resource and holder = p_holder returning * into r;
  if r.resource is null then
    select * into r from public.agent_leases where resource = p_resource;
    return jsonb_build_object('ok', false, 'reason', case when r.resource is null then 'no such lease' else 'held by someone else' end, 'holder', r.holder);
  end if;
  return jsonb_build_object('ok', true, 'expires_at', r.expires_at);
end $$;

create or replace function public.lease_release(p_key text, p_resource text, p_holder text, p_force boolean default false)
returns jsonb language plpgsql security definer set search_path = public as $$
declare n int;
begin
  if not public.team_ok(p_key) then raise exception 'bad team key'; end if;
  delete from public.agent_leases where resource = p_resource and (p_force or holder = p_holder);
  get diagnostics n = row_count;
  return jsonb_build_object('ok', n > 0, 'released', n);
end $$;

create or replace function public.lease_list(p_key text)
returns setof public.agent_leases language plpgsql security definer stable set search_path = public as $$
begin
  if not public.team_ok(p_key) then raise exception 'bad team key'; end if;
  return query select * from public.agent_leases where expires_at > now() order by acquired_at;
end $$;

create or replace function public.cursor_get_all(p_key text)
returns setof public.collect_cursors language plpgsql security definer stable set search_path = public as $$
begin
  if not public.team_ok(p_key) then raise exception 'bad team key'; end if;
  return query select * from public.collect_cursors order by name;
end $$;

create or replace function public.cursor_set(p_key text, p_name text, p_value bigint, p_by text default null, p_force boolean default false)
returns jsonb language plpgsql security definer set search_path = public as $$
declare old bigint;
begin
  if not public.team_ok(p_key) then raise exception 'bad team key'; end if;
  select value into old from public.collect_cursors where name = p_name;
  if old is not null and not p_force and p_value <= old then
    return jsonb_build_object('changed', false, 'value', old);
  end if;
  insert into public.collect_cursors (name, value, updated_by, updated_at) values (p_name, p_value, p_by, now())
  on conflict (name) do update set value = excluded.value, updated_by = excluded.updated_by, updated_at = now();
  return jsonb_build_object('changed', true, 'value', p_value, 'previous', old);
end $$;

grant execute on function public.lease_acquire(text, text, text, int, text) to anon, authenticated;
grant execute on function public.lease_renew(text, text, text, int) to anon, authenticated;
grant execute on function public.lease_release(text, text, text, boolean) to anon, authenticated;
grant execute on function public.lease_list(text) to anon, authenticated;
grant execute on function public.cursor_get_all(text) to anon, authenticated;
grant execute on function public.cursor_set(text, text, bigint, text, boolean) to anon, authenticated;
