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
end
$do$;

create table if not exists public.stocks (
  id bigserial primary key,
  symbol varchar(20) not null,
  name text not null,
  market public.market_type not null,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint stocks_symbol_market_uq
    unique (symbol, market)
);

create index if not exists stocks_market_idx on public.stocks (market);
create index if not exists stocks_is_active_idx on public.stocks (is_active);

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
