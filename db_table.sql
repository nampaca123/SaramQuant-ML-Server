do $do$
begin
  if not exists (select 1 from pg_type where typname = 'market_type') then
    create type public.market_type as enum ('KR_KOSPI','KR_KOSDAQ','US_NYSE','US_NASDAQ');
  end if;

  if not exists (select 1 from pg_type where typname = 'direction_type') then
    create type public.direction_type as enum ('UP','DOWN');
  end if;

  if not exists (select 1 from pg_type where typname = 'benchmark_type') then
    create type public.benchmark_type as enum ('KR_KOSPI','KR_KOSDAQ','US_SP500','US_NASDAQ');
  end if;

  if not exists (select 1 from pg_type where typname = 'country_type') then
    create type public.country_type as enum ('KR','US');
  end if;

  if not exists (select 1 from pg_type where typname = 'maturity_type') then
    create type public.maturity_type as enum ('91D','1Y','3Y','10Y');
  end if;

  if not exists (select 1 from pg_type where typname = 'report_type') then
    create type public.report_type as enum ('Q1','Q2','Q3','FY');
  end if;

  if not exists (select 1 from pg_type where typname = 'data_coverage_type') then
    create type public.data_coverage_type as enum ('FULL','LOSS','PARTIAL','INSUFFICIENT','NO_FS');
  end if;
end
$do$;

create table if not exists public.stocks (
  id bigserial primary key,
  symbol varchar(20) not null,
  name text not null,
  market public.market_type not null,
  is_active boolean not null default true,
  dart_corp_code varchar(8),
  sector varchar(100),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint stocks_symbol_market_uq
    unique (symbol, market)
);

create index if not exists stocks_market_idx on public.stocks (market);
create index if not exists stocks_is_active_idx on public.stocks (is_active);
create index if not exists stocks_dart_corp_code_idx on public.stocks (dart_corp_code)
  where dart_corp_code is not null;
create index if not exists stocks_sector_null_idx on public.stocks (market)
  where sector is null and is_active = true;
create index if not exists stocks_sector_idx on public.stocks (sector)
  where sector is not null;

create table if not exists public.daily_prices (
  id bigserial primary key,
  stock_id bigint not null references public.stocks(id) on delete cascade,
  date date not null,
  open numeric(15,2) not null,
  high numeric(15,2) not null,
  low numeric(15,2) not null,
  close numeric(15,2) not null,
  volume bigint not null,
  created_at timestamptz not null default now(),
  constraint daily_prices_ohlc_chk
    check (open >= 0 and high >= 0 and low >= 0 and close >= 0),
  constraint daily_prices_hl_chk
    check (high >= low),
  constraint daily_prices_volume_chk
    check (volume >= 0),
  constraint daily_prices_stock_date_uq
    unique (stock_id, date)
);

create index if not exists daily_prices_stock_date_idx
  on public.daily_prices (stock_id, date desc);
create index if not exists daily_prices_date_idx
  on public.daily_prices (date desc);

create table if not exists public.predictions (
  id bigserial primary key,
  stock_id bigint not null references public.stocks(id) on delete cascade,
  date date not null,
  direction public.direction_type not null,
  confidence numeric(5,4) not null,
  actual_direction public.direction_type,
  is_correct boolean,
  created_at timestamptz not null default now(),
  constraint predictions_confidence_chk
    check (confidence >= 0 and confidence <= 1),
  constraint predictions_stock_date_uq
    unique (stock_id, date)
);

create index if not exists predictions_stock_date_idx
  on public.predictions (stock_id, date desc);
create index if not exists predictions_is_correct_idx
  on public.predictions (is_correct);

create table if not exists public.ml_models (
  id bigserial primary key,
  name varchar(50) not null,
  market public.market_type not null,
  accuracy numeric(5,4),
  path varchar(255) not null,
  is_active boolean not null default false,
  created_at timestamptz not null default now(),
  constraint ml_models_accuracy_chk
    check (accuracy is null or (accuracy >= 0 and accuracy <= 1))
);

create index if not exists ml_models_market_idx on public.ml_models (market);
create index if not exists ml_models_is_active_idx on public.ml_models (is_active);

create unique index if not exists ml_models_one_active_per_market_uq
  on public.ml_models (market)
  where is_active = true;

create table if not exists public.benchmark_daily_prices (
  id bigserial primary key,
  benchmark public.benchmark_type not null,
  date date not null,
  close numeric(15,2) not null,
  created_at timestamptz not null default now(),
  constraint benchmark_daily_prices_benchmark_date_uq
    unique (benchmark, date),
  constraint benchmark_daily_prices_close_chk
    check (close >= 0)
);

create index if not exists benchmark_daily_prices_benchmark_date_idx
  on public.benchmark_daily_prices (benchmark, date desc);
create index if not exists benchmark_daily_prices_date_idx
  on public.benchmark_daily_prices (date desc);

create table if not exists public.risk_free_rates (
  id bigserial primary key,
  country public.country_type not null,
  maturity public.maturity_type not null,
  date date not null,
  rate numeric(6,4) not null,
  created_at timestamptz not null default now(),
  constraint risk_free_rates_country_maturity_date_uq
    unique (country, maturity, date),
  constraint risk_free_rates_rate_chk
    check (rate >= -10 and rate <= 100)
);

create index if not exists risk_free_rates_country_maturity_date_idx
  on public.risk_free_rates (country, maturity, date desc);
create index if not exists risk_free_rates_date_idx
  on public.risk_free_rates (date desc);

create table if not exists public.stock_indicators (
  stock_id   bigint not null references public.stocks(id) on delete cascade,
  date       date not null,
  sma_20     numeric(15,4),
  ema_20     numeric(15,4),
  wma_20     numeric(15,4),
  rsi_14     numeric(8,4),
  macd       numeric(15,4),
  macd_signal numeric(15,4),
  macd_hist  numeric(15,4),
  stoch_k    numeric(8,4),
  stoch_d    numeric(8,4),
  bb_upper   numeric(15,4),
  bb_middle  numeric(15,4),
  bb_lower   numeric(15,4),
  atr_14     numeric(15,4),
  adx_14     numeric(8,4),
  plus_di    numeric(8,4),
  minus_di   numeric(8,4),
  obv        bigint,
  vma_20     bigint,
  sar        numeric(15,4),
  beta       numeric(8,4),
  alpha      numeric(8,4),
  sharpe     numeric(8,4),
  created_at timestamptz not null default now(),
  constraint stock_indicators_pkey
    primary key (stock_id, date)
);

create index if not exists idx_stock_indicators_date
  on public.stock_indicators (date desc);
create index if not exists idx_stock_indicators_stock_date
  on public.stock_indicators (stock_id, date desc);

create table if not exists public.financial_statements (
  id bigserial primary key,
  stock_id bigint not null references public.stocks(id) on delete cascade,
  fiscal_year int not null,
  report_type public.report_type not null,
  revenue numeric(20,2),
  operating_income numeric(20,2),
  net_income numeric(20,2),
  total_assets numeric(20,2),
  total_liabilities numeric(20,2),
  total_equity numeric(20,2),
  shares_outstanding bigint,
  created_at timestamptz not null default now(),
  constraint financial_statements_uq
    unique (stock_id, fiscal_year, report_type)
);

create index if not exists financial_statements_stock_id_idx
  on public.financial_statements (stock_id);
create index if not exists financial_statements_fiscal_year_idx
  on public.financial_statements (fiscal_year desc, report_type);

create table if not exists public.stock_fundamentals (
  stock_id bigint not null references public.stocks(id) on delete cascade,
  date date not null,
  per numeric(12,4),
  pbr numeric(12,4),
  eps numeric(15,4),
  bps numeric(15,4),
  roe numeric(10,4),
  debt_ratio numeric(10,4),
  operating_margin numeric(10,4),
  data_coverage public.data_coverage_type not null default 'FULL',
  created_at timestamptz not null default now(),
  constraint stock_fundamentals_pkey
    primary key (stock_id, date)
);

create index if not exists idx_stock_fundamentals_date
  on public.stock_fundamentals (date desc);
create index if not exists idx_stock_fundamentals_stock_date
  on public.stock_fundamentals (stock_id, date desc);

do $do$
begin
  if not exists (
    select 1
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where p.proname = 'set_updated_at'
      and n.nspname = 'public'
  ) then
    execute $sql$
      create function public.set_updated_at()
      returns trigger
      language plpgsql
      as $func$
      begin
        new.updated_at = now();
        return new;
      end;
      $func$;
    $sql$;
  end if;

  if not exists (
    select 1
    from pg_trigger t
    join pg_class c on c.oid = t.tgrelid
    join pg_namespace n on n.oid = c.relnamespace
    where t.tgname = 'trg_stocks_updated_at'
      and n.nspname = 'public'
      and c.relname = 'stocks'
  ) then
    execute $sql$
      create trigger trg_stocks_updated_at
      before update on public.stocks
      for each row execute function public.set_updated_at();
    $sql$;
  end if;
end
$do$;

-- ============================================================
-- User / Auth tables
-- ============================================================

do $do$
begin
  if not exists (select 1 from pg_type where typname = 'oauth_provider_type') then
    create type public.oauth_provider_type as enum ('GOOGLE','KAKAO');
  end if;

  if not exists (select 1 from pg_type where typname = 'gender_type') then
    create type public.gender_type as enum ('MALE','FEMALE','UNSPECIFIED');
  end if;

  if not exists (select 1 from pg_type where typname = 'investment_experience_type') then
    create type public.investment_experience_type as enum ('BEGINNER','INTERMEDIATE','ADVANCED');
  end if;

  if not exists (select 1 from pg_type where typname = 'user_role_type') then
    create type public.user_role_type as enum ('STANDARD','ADMIN');
  end if;
end
$do$;

create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  email varchar(255) not null unique,
  name varchar(100) not null,
  provider public.oauth_provider_type not null,
  provider_id varchar(255) not null,
  role public.user_role_type not null default 'STANDARD',
  created_at timestamptz not null default now(),
  last_login_at timestamptz not null default now(),
  constraint users_provider_uq unique (provider, provider_id)
);

create table if not exists public.user_profiles (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null unique references public.users(id) on delete cascade,
  nickname varchar(30),
  birth_year int,
  gender public.gender_type,
  profile_image_url text,
  investment_experience public.investment_experience_type not null default 'BEGINNER',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.user_preferred_markets (
  user_profile_id uuid not null references public.user_profiles(id) on delete cascade,
  market market_type not null,
  primary key (user_profile_id, market)
);

create table if not exists public.refresh_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  token_hash varchar(64) not null unique,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  revoked_at timestamptz
);

create index if not exists refresh_tokens_user_id_idx
  on public.refresh_tokens (user_id);
create index if not exists refresh_tokens_active_idx
  on public.refresh_tokens (expires_at)
  where revoked_at is null;

do $do$
begin
  if not exists (
    select 1
    from pg_trigger t
    join pg_class c on c.oid = t.tgrelid
    where t.tgname = 'trg_user_profiles_updated_at'
      and c.relname = 'user_profiles'
  ) then
    execute $sql$
      create trigger trg_user_profiles_updated_at
      before update on public.user_profiles
      for each row execute function public.set_updated_at();
    $sql$;
  end if;
end
$do$;
