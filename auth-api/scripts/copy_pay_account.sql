-- Run against pay-db, after copy_org.sql has created the new org in auth-db.
-- Copies the source org's pay setup onto the new org:
--   payment_accounts, cfs_accounts, statement_settings (current), statement_recipients.
-- Does NOT copy: account_fees, distribution_codes, invoices, payments, credits, statements, EFT links, refunds.
-- Edit the values below, then run the whole script.

DO $$
DECLARE
    v_schema          text := 'sandbox_pay';       -- schema holding the pay tables
    v_src_auth_id     text := 'SOURCE_ORG_ID';     -- auth org id of the account being copied
    v_new_auth_id     text := '10000080';          -- auth org id created by copy_org.sql
    v_new_name        text := 'New Account Name';  -- same name used in copy_org.sql
    v_new_branch_name text := NULL;                -- NULL keeps the source branch name

    -- Keys not present on a table are ignored by jsonb_populate_record.
    v_audit   jsonb := jsonb_build_object('version', 1, 'created_on', now(), 'updated_on', NULL);
    v_src_acc integer;
    v_new_acc integer;
    v_exists  boolean;
    v_tbl     text;
    v_pk      text;
    v_fk      text;
    v_filter  text;
    r         record;
BEGIN
    EXECUTE format('SELECT id FROM %I.payment_accounts WHERE auth_account_id = $1', v_schema)
    INTO v_src_acc USING v_src_auth_id;
    IF v_src_acc IS NULL THEN
        RAISE EXCEPTION 'No payment account for source auth account %', v_src_auth_id;
    END IF;

    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.payment_accounts WHERE auth_account_id = $1)', v_schema)
    INTO v_exists USING v_new_auth_id;
    IF v_exists THEN
        RAISE EXCEPTION 'Payment account already exists for auth account %', v_new_auth_id;
    END IF;

    -- Payment account
    EXECUTE format('INSERT INTO %1$I.payment_accounts SELECT * FROM jsonb_populate_record(NULL::%1$I.payment_accounts,
                        (SELECT to_jsonb(t) FROM %1$I.payment_accounts t WHERE t.id = $1) || $2) RETURNING id', v_schema)
    INTO v_new_acc
    USING v_src_acc,
          v_audit || jsonb_build_object('id', nextval(pg_get_serial_sequence(format('%I.payment_accounts', v_schema), 'id')),
                                        'auth_account_id', v_new_auth_id,
                                        'name', v_new_name,
                                        'has_nsf_invoices', NULL,
                                        'has_overdue_invoices', NULL,
                                        'eft_credit', 0,
                                        'ob_credit', 0,
                                        'pad_credit', 0)
                  || CASE WHEN v_new_branch_name IS NOT NULL
                          THEN jsonb_build_object('branch_name', v_new_branch_name) ELSE '{}'::jsonb END;

    -- Child tables: (table, primary key, column pointing at payment_accounts.id, extra filter)
    FOR v_tbl, v_pk, v_fk, v_filter IN
        SELECT * FROM (VALUES
            ('cfs_accounts',         'id', 'account_id',         'TRUE'),
            ('statement_settings',   'id', 'payment_account_id', 'to_date IS NULL'),
            ('statement_recipients', 'id', 'payment_account_id', 'TRUE')
        ) AS t(tbl, pk, fk, filter)
    LOOP
        FOR r IN EXECUTE format('SELECT %I AS id FROM %I.%I WHERE %I = $1 AND %s ORDER BY 1',
                                v_pk, v_schema, v_tbl, v_fk, v_filter)
                 USING v_src_acc LOOP
            EXECUTE format('INSERT INTO %1$I.%2$I SELECT * FROM jsonb_populate_record(NULL::%1$I.%2$I,
                                (SELECT to_jsonb(t) FROM %1$I.%2$I t WHERE t.%3$I = $1) || $2)', v_schema, v_tbl, v_pk)
            USING r.id,
                  v_audit || jsonb_build_object(v_pk, nextval(pg_get_serial_sequence(format('%I.%I', v_schema, v_tbl), v_pk)),
                                                v_fk, v_new_acc);
        END LOOP;
    END LOOP;

    RAISE NOTICE 'Copied payment account % (auth %) -> payment account % (auth %)',
        v_src_acc, v_src_auth_id, v_new_acc, v_new_auth_id;
END;
$$;
