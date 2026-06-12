alter table drivers
  add column if not exists default_bus_id uuid;

do $$
begin
  alter table drivers
    add constraint drivers_default_bus_school_fkey
    foreign key (default_bus_id, school_id)
    references buses(id, school_id)
    on delete set null (default_bus_id);
exception when duplicate_object then null;
end $$;

create index if not exists drivers_default_bus_id_idx
  on drivers (default_bus_id)
  where default_bus_id is not null;
