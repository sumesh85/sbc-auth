-- Copy an existing org (account) into a new org with a different name.
-- Copies: orgs, memberships, contacts + contact_links, product_subscriptions, org_settings, account_login_options.
-- Does NOT copy: affiliations, invitations, tasks, account linking keys, or anything in pay-db.
-- Edit the values below, then run the whole script.

DO $$
DECLARE
    v_schema          text    := 'sandbox_auth';      -- schema holding the auth tables
    v_src_org_id      integer := 1234;                -- source org id
    v_new_org_name    text    := 'New Account Name';  -- name for the copy
    v_new_branch_name text    := NULL;                -- NULL keeps the source branch name

    v_audit   jsonb := jsonb_build_object('version', 1, 'created', now(), 'modified', now());
    v_new_org integer;
    v_new_id  integer;
    v_tbl     text;
    v_exists  boolean;
    r         record;
BEGIN
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.orgs WHERE id = $1)', v_schema)
    INTO v_exists USING v_src_org_id;
    IF NOT v_exists THEN
        RAISE EXCEPTION 'Source org % does not exist in %', v_src_org_id, v_schema;
    END IF;

    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.orgs WHERE lower(name) = lower($1)
                                   AND status_code NOT IN (''INACTIVE'', ''REJECTED''))', v_schema)
    INTO v_exists USING v_new_org_name;
    IF v_exists THEN
        RAISE EXCEPTION 'An org named "%" already exists', v_new_org_name;
    END IF;

    -- Org
    EXECUTE format('INSERT INTO %1$I.orgs SELECT * FROM jsonb_populate_record(NULL::%1$I.orgs,
                        (SELECT to_jsonb(t) FROM %1$I.orgs t WHERE t.id = $1) || $2) RETURNING id', v_schema)
    INTO v_new_org
    USING v_src_org_id,
          v_audit || jsonb_build_object('id', nextval(pg_get_serial_sequence(format('%I.orgs', v_schema), 'id')),
                                        'uuid', gen_random_uuid(),
                                        'name', v_new_org_name)
                  || CASE WHEN v_new_branch_name IS NOT NULL
                          THEN jsonb_build_object('branch_name', v_new_branch_name) ELSE '{}'::jsonb END;

    -- Tables that only need org_id repointed
    FOREACH v_tbl IN ARRAY ARRAY['memberships', 'product_subscriptions', 'org_settings', 'account_login_options'] LOOP
        FOR r IN EXECUTE format('SELECT id FROM %I.%I WHERE org_id = $1 ORDER BY id', v_schema, v_tbl)
                 USING v_src_org_id LOOP
            EXECUTE format('INSERT INTO %1$I.%2$I SELECT * FROM jsonb_populate_record(NULL::%1$I.%2$I,
                                (SELECT to_jsonb(t) FROM %1$I.%2$I t WHERE t.id = $1) || $2)', v_schema, v_tbl)
            USING r.id,
                  v_audit || jsonb_build_object('id', nextval(pg_get_serial_sequence(format('%I.%I', v_schema, v_tbl), 'id')),
                                                'org_id', v_new_org);
        END LOOP;
    END LOOP;

    -- Contacts: clone each contact (so the two orgs don't share one), then the link
    FOR r IN EXECUTE format('SELECT id, contact_id FROM %I.contact_links WHERE org_id = $1 ORDER BY id', v_schema)
             USING v_src_org_id LOOP
        v_new_id := NULL;
        IF r.contact_id IS NOT NULL THEN
            EXECUTE format('INSERT INTO %1$I.contacts SELECT * FROM jsonb_populate_record(NULL::%1$I.contacts,
                                (SELECT to_jsonb(t) FROM %1$I.contacts t WHERE t.id = $1) || $2) RETURNING id', v_schema)
            INTO v_new_id
            USING r.contact_id,
                  v_audit || jsonb_build_object('id', nextval(pg_get_serial_sequence(format('%I.contacts', v_schema), 'id')));
        END IF;

        EXECUTE format('INSERT INTO %1$I.contact_links SELECT * FROM jsonb_populate_record(NULL::%1$I.contact_links,
                            (SELECT to_jsonb(t) FROM %1$I.contact_links t WHERE t.id = $1) || $2)', v_schema)
        USING r.id,
              v_audit || jsonb_build_object('id', nextval(pg_get_serial_sequence(format('%I.contact_links', v_schema), 'id')),
                                            'org_id', v_new_org,
                                            'contact_id', v_new_id);
    END LOOP;

    RAISE NOTICE 'Copied %.orgs % -> new org id %', v_schema, v_src_org_id, v_new_org;
END;
$$;
