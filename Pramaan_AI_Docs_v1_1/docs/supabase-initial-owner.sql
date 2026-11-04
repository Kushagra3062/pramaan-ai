-- Pramaan AI: one-time initial owner bootstrap
--
-- Do not run this against an unknown database. This script does not create a
-- Supabase Auth account or handle passwords. First create and confirm exactly
-- one owner account in Supabase Dashboard > Authentication > Users.
--
-- Then replace OWNER_EMAIL below in both marked places with that account's
-- email address and run the entire script in Supabase SQL Editor. The script
-- generates Pramaan's internal UUIDs, creates the first workspace, and grants
-- that account OWNER membership. It does not grant access to any other user.

BEGIN;

DO $bootstrap$
DECLARE
    -- Replace this one value with the confirmed Supabase Auth account email.
    owner_email text := 'OWNER_EMAIL';
    owner_display_name text;
    workspace_name text := 'Pramaan Demo';
    workspace_slug text := 'pramaan-demo';
    auth_subject_id uuid;
    pramaan_user_id uuid := gen_random_uuid();
    workspace_id uuid := gen_random_uuid();
    membership_id uuid := gen_random_uuid();
BEGIN
    -- Tolerate accidental copy/paste spaces around the email.
    owner_email := btrim(owner_email);
    IF owner_email = 'OWNER_EMAIL' THEN
        RAISE EXCEPTION 'Replace OWNER_EMAIL before running this script.';
    END IF;

    SELECT id INTO auth_subject_id
    FROM auth.users
    WHERE lower(email) = lower(owner_email)
    LIMIT 1;

    IF auth_subject_id IS NULL THEN
        RAISE EXCEPTION 'No Supabase Auth user matches %. Create and confirm that exact email under Authentication > Users first.', owner_email;
    END IF;

    IF EXISTS (SELECT 1 FROM public.users WHERE auth_subject = auth_subject_id::text) THEN
        RAISE EXCEPTION 'A Pramaan user already exists for this Supabase Auth account.';
    END IF;

    IF EXISTS (SELECT 1 FROM public.workspaces WHERE slug = workspace_slug) THEN
        RAISE EXCEPTION 'Workspace slug % already exists.', workspace_slug;
    END IF;

    owner_display_name := split_part(owner_email, '@', 1);

    INSERT INTO public.users (id, auth_subject, email, display_name, status)
    VALUES (pramaan_user_id, auth_subject_id::text, owner_email, owner_display_name, 'ACTIVE');

    INSERT INTO public.workspaces (id, name, slug)
    VALUES (workspace_id, workspace_name, workspace_slug);

    INSERT INTO public.workspace_members (id, workspace_id, user_id, role)
    VALUES (membership_id, workspace_id, pramaan_user_id, 'OWNER');
END
$bootstrap$;

-- Read-only confirmation. It must return exactly one row with role OWNER.
SELECT
    u.email,
    u.display_name,
    u.status,
    w.name AS workspace_name,
    w.slug AS workspace_slug,
    wm.role
FROM public.users AS u
JOIN public.workspace_members AS wm ON wm.user_id = u.id
JOIN public.workspaces AS w ON w.id = wm.workspace_id
WHERE lower(u.email) = lower(btrim('OWNER_EMAIL'))
  AND w.slug = 'pramaan-demo';

COMMIT;
