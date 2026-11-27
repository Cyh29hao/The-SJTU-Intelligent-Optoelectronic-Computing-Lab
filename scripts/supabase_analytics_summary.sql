-- Run in an isolated Supabase project first. The application opts in after installation.
begin;

do $$
begin
    if (
        select count(*)
        from information_schema.columns
        where table_schema = 'public' and table_name in ('page_views', 'resource_opens')
          and column_name = 'created_at'
          and data_type in ('timestamp with time zone', 'timestamp without time zone')
    ) <> 2 then
        raise exception 'Both analytics tables need a native PostgreSQL timestamp column named created_at';
    end if;
end;
$$;

create or replace function public.opticompt_analytics_summary(reference_time timestamp without time zone)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
with
    pv as materialized (
        select coalesce(path, '') as path,
               coalesce(page_type, '') as page_type,
               coalesce(item_id, '') as item_id,
               coalesce(title, '') as title,
               nullif(visitor_id, '') as visitor_id,
               created_at as original_stamp,
               date_trunc('second', case pg_typeof(created_at)
                   when 'timestamp with time zone'::regtype then created_at::timestamptz at time zone 'Asia/Shanghai'
                   else created_at::timestamp
               end) as stamp
        from public.page_views
    ),
    opens as materialized (
        select resource_id as id,
               coalesce(user_name, '') as name,
               coalesce(user_affiliation, '') as affiliation,
               coalesce(user_email, '') as email,
               date_trunc('second', case pg_typeof(created_at)
                   when 'timestamp with time zone'::regtype then created_at::timestamptz at time zone 'Asia/Shanghai'
                   else created_at::timestamp
               end) as stamp
        from public.resource_opens
        where nullif(resource_id, '') is not null
    ),
    page_totals as (
        select count(*) as total_views,
               count(distinct visitor_id) as unique_visitors,
               count(*) filter (where stamp >= reference_time - interval '7 days') as views_last_7_days,
               count(*) filter (where stamp::date = reference_time::date) as views_today,
               count(distinct visitor_id)
                   filter (where stamp >= reference_time - interval '7 days') as unique_visitors_last_7_days,
               count(*) filter (where page_type = 'article_detail') as article_detail_views
        from pv
    ),
    page_meta as (
        select distinct on (path) path, page_type, title
        from pv
        order by path, original_stamp nulls last
    ),
    pages as (
        select meta.path, meta.page_type, meta.title, counts.count, counts.last_viewed
        from (
            select path, count(*) as count, max(stamp) as last_viewed
            from pv
            group by path
        ) as counts
        join page_meta as meta using (path)
    ),
    articles as (
        select item_id as id, count(*) as count, max(stamp) as last_viewed
        from pv
        where page_type = 'article_detail'
        group by item_id
    ),
    resources as (
        select id, count(*) as count,
               count(distinct (name, affiliation, email)) as unique_count,
               max(stamp) as last_downloaded
        from opens
        group by id
    ),
    days as (
        select (reference_time::date - step) as day
        from generate_series(0, 6) as step
    ),
    page_days as (
        select stamp::date as day, count(*) as views
        from pv
        where stamp::date between reference_time::date - 6 and reference_time::date
        group by stamp::date
    ),
    open_days as (
        select stamp::date as day, count(*) as downloads
        from opens
        where stamp::date between reference_time::date - 6 and reference_time::date
        group by stamp::date
    ),
    trend as (
        select days.day, coalesce(page_days.views, 0) as views, coalesce(open_days.downloads, 0) as downloads
        from days
        left join page_days using (day)
        left join open_days using (day)
    )
select jsonb_build_object(
    'version', 1,
    'page_totals', (select to_jsonb(page_totals) from page_totals),
    'pages', coalesce((select jsonb_agg(to_jsonb(pages)) from pages), '[]'::jsonb),
    'articles', coalesce((select jsonb_agg(to_jsonb(articles)) from articles), '[]'::jsonb),
    'resources', coalesce((select jsonb_agg(to_jsonb(resources)) from resources), '[]'::jsonb),
    'trend', (select jsonb_agg(to_jsonb(trend) order by day) from trend)
);
$$;

revoke execute on function public.opticompt_analytics_summary(timestamp without time zone)
    from public, anon, authenticated;
grant execute on function public.opticompt_analytics_summary(timestamp without time zone)
    to service_role;

notify pgrst, 'reload schema';

commit;
