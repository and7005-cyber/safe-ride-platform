-- 003_local_snapshot.sql
-- Snapshot of the curated LOCAL safe-ride-platform database, captured to make
-- the deployed environment identical to local. Applied once (tracked as
-- seed:003_local_snapshot): it WIPES the data the earlier seeds (001/002)
-- loaded and replaces it with this exact snapshot. FK checks are disabled for
-- the load so insert order is irrelevant.
SET session_replication_role = replica;

DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT tablename FROM pg_tables
    WHERE schemaname = 'public'
      AND tablename NOT IN ('saferide_migrations', 'saferide_local_migrations')
  LOOP
    EXECUTE 'TRUNCATE TABLE public.' || quote_ident(r.tablename) || ' RESTART IDENTITY CASCADE';
  END LOOP;
END $$;

--
-- PostgreSQL database dump
--


-- Dumped from database version 16.13 (Debian 16.13-1.pgdg13+1)
-- Dumped by pg_dump version 16.13 (Debian 16.13-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: app_users; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.app_users (id, email, password_hash, full_name, phone, pin_hash, created_at) VALUES ('a0000000-0000-0000-0000-000000000001', 'admin@test.com', 'pbkdf2_sha256$200000$seedsaltadmin000$KE0yqk+lLnLKBtBTxl0WeQ++LBE9jr5S+ROr8LBrmeg=', 'Greenfield Admin', '+254700000001', NULL, '2026-06-15 06:14:40.562328+00');
INSERT INTO public.app_users (id, email, password_hash, full_name, phone, pin_hash, created_at) VALUES ('a0000000-0000-0000-0000-000000000005', 'mary@saferide.test', 'pbkdf2_sha256$200000$seedsaltdriver00$GmCr4H2kX/E4aT6whJEI/LDTB2xgfHaMtKXTTQlJ86w=', 'Mary Wanjiru', '+254700000005', 'hmac_sha256$4d09f578c0d3bfd445c3477650aa4c68ed6db13f27eae306e60eaf9c06c36efd', '2026-06-15 06:14:40.562328+00');
INSERT INTO public.app_users (id, email, password_hash, full_name, phone, pin_hash, created_at) VALUES ('a0000000-0000-0000-0000-000000000002', 'and7005@gmail.com', 'pbkdf2_sha256$200000$seedsaltparent00$fq8xMN0hAmQISgkN5BnsPiz5JjH4rczW1YKlEZZwlfo=', 'Amina Achieng', '+254700000002', NULL, '2026-06-15 06:14:40.562328+00');
INSERT INTO public.app_users (id, email, password_hash, full_name, phone, pin_hash, created_at) VALUES ('d94fa87f-f565-46e1-a1c2-35fa5e14a221', 'kenesa@example.com', 'pbkdf2_sha256$200000$5ab6180f7bb6a1529a279ab1795b3bf7$pVd2bebUA17mSNeecFEHYsHYEBznoY89VnkdBcrij+o=', 'Joseph Kenesa', NULL, NULL, '2026-06-15 15:06:00.748374+00');
INSERT INTO public.app_users (id, email, password_hash, full_name, phone, pin_hash, created_at) VALUES ('25f9bc97-1446-4487-9633-2756b5ba0ba0', 'lucy@example.com', 'pbkdf2_sha256$200000$7ce6c70239d0189194d0d815c13ebbd6$RO4o7gQfVyJ8gCKtxCTkv2gkpUQEefJp8ZuExFNyMmA=', 'Lucy Mwangi', NULL, NULL, '2026-06-15 15:08:00.068814+00');
INSERT INTO public.app_users (id, email, password_hash, full_name, phone, pin_hash, created_at) VALUES ('a0000000-0000-0000-0000-000000000004', 'francis@saferide.test', 'pbkdf2_sha256$200000$seedsaltdriver00$GmCr4H2kX/E4aT6whJEI/LDTB2xgfHaMtKXTTQlJ86w=', 'Francis Ochieng', '+254700000004', 'hmac_sha256$3f481ae5a8b9fa820f486a6e2c742efe6fbfb46bb98931ab77aade1edbd2cc0a', '2026-06-15 06:14:40.562328+00');
INSERT INTO public.app_users (id, email, password_hash, full_name, phone, pin_hash, created_at) VALUES ('a0000000-0000-0000-0000-000000000003', 'and7005@yahoo.it', 'pbkdf2_sha256$200000$seedsaltdriver00$GmCr4H2kX/E4aT6whJEI/LDTB2xgfHaMtKXTTQlJ86w=', 'Daniel Kamau', '+254700000003', 'hmac_sha256$e41edd9db7b19f56dd1396dfafabc612d805c6050da15cc6b867e42a2bc0bb19', '2026-06-15 06:14:40.562328+00');


--
-- Data for Name: app_user_roles; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.app_user_roles (user_id, role) VALUES ('a0000000-0000-0000-0000-000000000001', 'admin');
INSERT INTO public.app_user_roles (user_id, role) VALUES ('a0000000-0000-0000-0000-000000000002', 'parent');
INSERT INTO public.app_user_roles (user_id, role) VALUES ('a0000000-0000-0000-0000-000000000003', 'driver');
INSERT INTO public.app_user_roles (user_id, role) VALUES ('a0000000-0000-0000-0000-000000000004', 'driver');
INSERT INTO public.app_user_roles (user_id, role) VALUES ('a0000000-0000-0000-0000-000000000005', 'driver');
INSERT INTO public.app_user_roles (user_id, role) VALUES ('d94fa87f-f565-46e1-a1c2-35fa5e14a221', 'parent');
INSERT INTO public.app_user_roles (user_id, role) VALUES ('25f9bc97-1446-4487-9633-2756b5ba0ba0', 'parent');


--
-- Data for Name: schools; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.schools (id, name, approaching_threshold, default_inter_student_minutes, created_at) VALUES ('11111111-1111-1111-1111-111111111111', 'Greenfield Academy', 2, 6, '2026-06-15 06:14:40.454864+00');


--
-- Data for Name: audit_log; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: auth_sessions; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('23dbbf31-eb7f-4294-a645-cd0aa4323156', 'a0000000-0000-0000-0000-000000000001', '73c31ceae86d771bed056f12dbcc4b3283398e8c3988d3620ee7b614f33d8737', '2026-06-16 11:07:40.144939+00', NULL, '2026-06-15 19:07:40.084296+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('2cfcd6d9-456c-4731-b85d-bf59d0db0ce1', 'a0000000-0000-0000-0000-000000000001', 'a54fbc2ffc0ddd5155987757a2e5bd840747ff8e4c59e71d5b6b2826647d25f1', '2026-06-16 07:16:16.652999+00', NULL, '2026-06-15 15:16:16.445011+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('3df35085-7fa1-42eb-b5c8-66ba63512974', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', '6ac3b5f50672fa71a6270f2da4a0f200413018bed0a2814837038ca1d1ba7521', '2026-06-16 07:06:15.878722+00', '2026-06-15 15:06:18.201569+00', '2026-06-15 15:06:00.783951+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('c91ae37d-9f30-4df1-b4e2-113619e8f275', 'a0000000-0000-0000-0000-000000000001', '842f5ac71eef9b261b6224d9525b0f791460d107053ce8df295f6d57661697d0', '2026-06-16 07:19:46.567124+00', NULL, '2026-06-15 15:19:46.557434+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('d401a10d-c715-447f-a09c-96882d9d8d7d', 'a0000000-0000-0000-0000-000000000002', 'd748d2b52ecaa1f0063dc37fd186e95aaedcfc7bf5e5f85431a33b7b97d58f4f', '2026-06-16 07:19:46.61955+00', NULL, '2026-06-15 15:19:46.610101+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('363bbe77-150a-49f0-9a1b-47e2697e04db', 'a0000000-0000-0000-0000-000000000003', '18734b73a6ecfd6476d33abfbaf0bb1dd83e7ca6ee8a523b9dc8fb30a9f0a2be', '2026-06-16 07:19:09.824406+00', NULL, '2026-06-15 15:19:09.5427+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('0e7ce018-0846-4699-a98b-5521602bb739', 'a0000000-0000-0000-0000-000000000001', '080952aa61fd8e9085c9efeeb8734ac2c80748052e79e17baa7cb37f4ce80e98', '2026-06-18 02:52:28.035794+00', '2026-06-17 10:52:28.662636+00', '2026-06-17 10:51:57.974293+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('e4f7bfaa-fa31-42c3-ae4c-4f31d74121e7', 'a0000000-0000-0000-0000-000000000001', '5fcda3bb39ca988db232f47a678db314ea7974842d388da7c602a75371d613d7', '2026-06-16 07:06:31.745891+00', '2026-06-15 15:06:35.336953+00', '2026-06-15 15:06:31.728677+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('25e77d76-1a87-4908-b13f-8ca9f1b9846d', 'a0000000-0000-0000-0000-000000000001', '3bf22b7de0cc2610b834232a7733636982bf936315860b49c5eedd8a29e975eb', '2026-06-16 11:08:59.283674+00', NULL, '2026-06-15 19:08:59.273791+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('36f266e6-7355-4a71-ada6-33b13d5f6038', 'a0000000-0000-0000-0000-000000000002', 'e48acaebb947266993f13849ba13e10b806762271bf2ce869cfc51ff0a1f9eef', '2026-06-15 22:18:56.629608+00', '2026-06-15 06:18:59.540606+00', '2026-06-15 06:18:14.11874+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('c6c0c640-b88a-40b1-b63b-79b7ba436caa', 'a0000000-0000-0000-0000-000000000001', 'c8ade081b141046bdb7f6694eb9c1832c4d8a5548a9e3dd42bc92ed4c45c8d77', '2026-06-15 22:20:40.265343+00', NULL, '2026-06-15 06:20:40.265343+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('04c2182b-1582-4566-8cf8-40224675218d', 'a0000000-0000-0000-0000-000000000002', 'ac0c558107186e6a4b5af68d1c46e7dc4e489701693de44080e5f6964b6c5eee', '2026-06-16 07:19:10.384848+00', NULL, '2026-06-15 15:19:09.539681+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('a29b9805-a087-4aa0-ae68-387693443f95', 'a0000000-0000-0000-0000-000000000001', '1f69301989938ac3fc648fc24e40eff65adda7795adb3342edfea0698eda367d', '2026-06-16 07:18:38.894525+00', NULL, '2026-06-15 15:18:38.82278+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('d37a173e-0ec9-45e4-aca5-18e4b7b879db', 'a0000000-0000-0000-0000-000000000001', '96195206b41a23a0bf22192375f152d348249c1588c2409cd8e0bf2e48cae4c9', '2026-06-16 07:07:30.477837+00', '2026-06-15 15:07:37.426265+00', '2026-06-15 15:06:38.71907+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('a63cf5eb-5c36-4071-bbf5-7cefc47bd5da', '25f9bc97-1446-4487-9633-2756b5ba0ba0', 'dff54c5e0da4df9e17b099862f6706c0af7f00c36eb41a487cd47b45aea6e294', '2026-06-16 07:08:00.070212+00', NULL, '2026-06-15 15:08:00.070212+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('ca444d26-95d9-4170-ba25-f1a49da0d4c9', 'a0000000-0000-0000-0000-000000000001', '29511d5eece55695de3aa2e297e07d9a2c936ea7d2421276ae3baabd6a80ad9d', '2026-06-16 11:07:54.840046+00', NULL, '2026-06-15 19:07:54.522977+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('def9f94f-0460-4bb4-adc3-e00c81ee00ea', '25f9bc97-1446-4487-9633-2756b5ba0ba0', '48560dd8d30f85b7cbcc451fcd471f8e8a84910a402174aeb816013e66b95d11', '2026-06-16 07:08:05.145902+00', '2026-06-15 15:08:06.09976+00', '2026-06-15 15:08:00.103944+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('4d9330dd-fb95-4b7a-b13b-20a4a5e158db', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', 'c368156faf74b66f146d4ba9b7b8d0f8ceeb1f04fff311f818dc28515ce0182c', '2026-06-16 07:08:17.252409+00', '2026-06-15 15:08:21.744801+00', '2026-06-15 15:08:11.493818+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('37426251-a710-4659-83b3-104d8618dd26', 'a0000000-0000-0000-0000-000000000002', '6b572a26598a71a9093765957f4ca8dc3667e1a3c1c8e8686b4c0dd8f3488e87', '2026-06-16 07:19:09.571642+00', '2026-06-15 15:19:09.573058+00', '2026-06-15 15:19:09.570233+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('1d2c5c42-6c91-4da6-8ce6-536d716ad217', 'a0000000-0000-0000-0000-000000000001', 'fc0767e75fbe545da08e09db4564459ccbc610ef485453add31a297821a04b51', '2026-06-16 11:04:46.529941+00', NULL, '2026-06-15 19:04:46.375337+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('9012a930-dab6-4b5b-a25a-1d933cef4832', 'a0000000-0000-0000-0000-000000000001', '13e152e894fffcc52f059bc85d5bd353c05e305d0dd6bd4f86e13bfce02b723d', '2026-06-16 07:05:16.859688+00', '2026-06-15 15:05:30.913636+00', '2026-06-15 06:21:47.749868+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('27dceb98-b584-495e-81be-ffa6e6e8cbb1', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', 'eb57aa2a152dfd67a75aca74268a24483ad5093a7cd6b72172d6ce7746a79919', '2026-06-16 07:06:00.750024+00', NULL, '2026-06-15 15:06:00.750024+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('d256b4d8-64b2-49f4-8098-b66f2e75059b', 'a0000000-0000-0000-0000-000000000002', '03453f16adc12ebfb44c4e5ebece652986bfcadc9dff9877e054c2b57ccdd09b', '2026-06-16 11:07:55.168065+00', '2026-06-15 19:07:55.169408+00', '2026-06-15 19:07:55.166539+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('e8068c54-93e7-40aa-89df-35244a1548a6', 'a0000000-0000-0000-0000-000000000003', '0f5ffeadd0e93b05576583b87f5649c89967fde3aecc266b74c307859929e8fd', '2026-06-18 02:51:28.029719+00', '2026-06-17 10:51:31.643109+00', '2026-06-17 10:49:47.091904+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('27d4c2c3-94eb-4e41-88c6-18846081cbc1', 'a0000000-0000-0000-0000-000000000001', 'da9f1b92a2f22f4e215e51ef1cc347064316c1c29ab65daab52fad4434c0532a', '2026-06-16 12:04:43.335549+00', NULL, '2026-06-15 15:08:28.184888+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('c87a5fba-fb4a-416d-bcfd-16d1a6e19547', 'a0000000-0000-0000-0000-000000000001', '286644b3d68e491f1d0c9eee891cbf86673fbff72ce1a9078c61299ba2c44b3f', '2026-06-16 07:16:00.304067+00', NULL, '2026-06-15 15:16:00.263096+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('0027d004-190a-4d8e-b057-f8055c17d098', 'a0000000-0000-0000-0000-000000000001', '302dbb6a03ab1a8de7124c9ea86bf5354330777bd72cfef5b6fc35aafb666b0a', '2026-06-17 12:45:40.839697+00', NULL, '2026-06-16 20:45:40.806994+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('8d00f253-5535-447f-ae6b-5bfae039afc1', 'a0000000-0000-0000-0000-000000000001', '614eedef8caec8d437bca9b14d6d9484431bd6423e0088bbb814624c6a493c5c', '2026-06-16 11:07:55.703448+00', NULL, '2026-06-15 19:07:55.096572+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('a84bc2df-5439-4ce9-af6a-e184e03ed98b', 'a0000000-0000-0000-0000-000000000001', '41e33f4a0ced942b0b48c431056f31a4150f06f25bcfee1530e6f0057f6e9d37', '2026-06-16 07:19:09.765745+00', NULL, '2026-06-15 15:19:09.512401+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('3bdd17a2-caf4-49f0-a2ae-553bbc3454df', 'a0000000-0000-0000-0000-000000000001', '7e579b852501729543934e9d0f1626aa4700a086c65d4019215bb5afd326bb6a', '2026-06-16 07:19:10.654575+00', NULL, '2026-06-15 15:19:10.618823+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('fb922074-c91b-43e9-8716-18a81548df73', 'a0000000-0000-0000-0000-000000000001', '671a0611ff0e1fb3b1e353b61672ab20bc0ad7baf1f7567b9cdd83d4d71a9c40', '2026-06-17 12:02:43.59803+00', NULL, '2026-06-16 20:02:43.567842+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('79988757-736d-4767-a86d-842ae3d3d341', 'a0000000-0000-0000-0000-000000000001', '06f1f596a70cca8a341959faeef29646074a5f2cdfc2c438a8fcb20965db4628', '2026-06-17 12:25:59.211278+00', NULL, '2026-06-16 20:25:59.182101+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('023704cb-7b66-4b02-86af-f5f554f0a3a4', 'a0000000-0000-0000-0000-000000000002', '257d51851c628fdb0fade0d74444e53037171b16563565b80b610c7cc5e6edb1', '2026-06-16 11:07:55.759145+00', NULL, '2026-06-15 19:07:55.122908+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('821e4619-10ab-4345-b5b0-4d5c515431b1', 'a0000000-0000-0000-0000-000000000003', '4330864fc9e03dba7dc81321008b6ce51e82f22b673f6df153c99e58c614f25a', '2026-06-17 12:07:58.370018+00', '2026-06-16 20:07:58.985134+00', '2026-06-16 20:07:20.680682+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('23377d06-0888-4c22-a975-6cfb0c58dfd9', 'a0000000-0000-0000-0000-000000000001', '24392b151f7262420de25f4ddc325df91da5dfef3611e184186a2cf56db28d7b', '2026-06-16 11:08:30.600363+00', NULL, '2026-06-15 19:08:30.600363+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('a8a65de0-a9de-47bb-9cc6-53c4c98458fa', 'a0000000-0000-0000-0000-000000000002', 'a8b10e416a254f90a799733514c65e746b096b0fdebbbdd583b059053a7c4d26', '2026-06-16 11:08:30.631512+00', NULL, '2026-06-15 19:08:30.631512+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('acdb029a-44eb-45f7-9ed8-672f618cba90', 'a0000000-0000-0000-0000-000000000001', '4e8a5dcde3129c11ba08a68b8ce413ad771fb13fdd2a09a0ab7b0aa486c4d545', '2026-06-17 12:45:39.930472+00', NULL, '2026-06-16 20:45:39.909827+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('54c896b2-28e8-4fb9-be98-57d868936542', 'a0000000-0000-0000-0000-000000000001', 'e6f0e8e51c1bb9b459d424820a2a61ba2de442f94eb0daf07bbf7e957e639306', '2026-06-17 12:07:13.558343+00', '2026-06-16 20:07:15.116299+00', '2026-06-16 20:06:28.478879+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('17f62f31-981c-49c8-821d-31d56976d23c', 'a0000000-0000-0000-0000-000000000001', 'f16be369f9830e182220ca892f28376dbcb0c57c03e0b7154e156d818b77f244', '2026-06-17 12:02:46.065792+00', NULL, '2026-06-16 20:02:44.830258+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('557e4fab-a903-4f5b-a344-339c60b88802', 'a0000000-0000-0000-0000-000000000003', 'e527bdef8ce48c9faeae3a7381f0cf0bc41ab38da0ed619f790d8ff9bf740a1c', '2026-06-17 12:22:04.30732+00', '2026-06-16 20:22:07.879362+00', '2026-06-16 20:21:59.117377+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('79f944a9-a181-455f-b214-a12b55718fd5', 'a0000000-0000-0000-0000-000000000001', '180bd386b6d3a03fa608727195b984bb76416f403d8985a0544ef2a588cd48b1', '2026-06-17 12:47:06.369311+00', NULL, '2026-06-16 20:47:06.347313+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('353b7ee9-deb5-440c-bb3c-f0c48f363f57', 'a0000000-0000-0000-0000-000000000001', '990b1134c07a01356288039a37efaeb1441d348faa5582bcf7686175646fe327', '2026-06-17 12:21:52.346382+00', '2026-06-16 20:21:53.503179+00', '2026-06-16 20:08:05.053157+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('4b7350bb-f03a-4e53-9f97-8f3af33f6102', 'a0000000-0000-0000-0000-000000000001', '2203fb1669d03494d7c6a57d9d68ba03455e2a4041f8130a7dd3250d49d889c7', '2026-06-18 02:51:49.355979+00', '2026-06-17 10:51:51.328089+00', '2026-06-17 10:51:34.31128+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('949dea16-116c-4fd5-8c15-116d7865ba83', 'a0000000-0000-0000-0000-000000000001', '1bd6a084393c4e8cc7920ce51783fe6a75c5166a48a4b40648a2f1663b45663a', '2026-06-17 12:47:32.439823+00', NULL, '2026-06-16 20:47:32.351467+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('cc3cdba1-b54e-4339-92de-da79eb01732f', 'a0000000-0000-0000-0000-000000000001', 'fdf6124b4e7478d6fd3bf4d8d571d7ac6196126e6f747b541447bcda6cae3e76', '2026-06-17 12:47:07.656786+00', NULL, '2026-06-16 20:47:07.518299+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('d4e1abd6-1a7d-4dca-949a-7e067aa832d3', 'a0000000-0000-0000-0000-000000000001', 'a3908e4cb7521565e6f2c5b21e2d94f9ea67d99d0a9a74cd9b4bb3982256a6fb', '2026-06-18 02:49:40.353136+00', '2026-06-17 10:49:40.727638+00', '2026-06-16 20:22:13.128807+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('0a3324d3-2bf3-4bcd-ab1c-b95928bcfb21', 'a0000000-0000-0000-0000-000000000003', '2855144649ef87691288a2544f466aa8767e1b8eb20c20957ddafbd9f753c78a', '2026-06-18 02:52:39.08944+00', '2026-06-17 10:52:42.963168+00', '2026-06-17 10:52:34.179744+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('d31bf4c2-7e8a-4fd3-965e-9e87e1468914', 'a0000000-0000-0000-0000-000000000001', 'fe5d96a64ec1f21a896f2d5295353c1a1dce2dff768b8da0c233b14b74a28a07', '2026-06-18 03:02:05.84374+00', '2026-06-17 11:02:07.694807+00', '2026-06-17 10:52:45.353227+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('9e831fc8-dfe1-4f3a-9d7b-c1b5af478d8f', 'a0000000-0000-0000-0000-000000000002', 'a1faf6bd5a22babf45ebc453722eb8864823c69988e4e5ed4dc3aad81fa9bd3c', '2026-06-18 03:04:15.415875+00', '2026-06-17 11:04:17.505409+00', '2026-06-17 11:02:14.294589+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('881ec571-09d4-43b0-b8f7-73bb766c9eaa', 'a0000000-0000-0000-0000-000000000001', 'ad42fb0c89b186feb6acbde18d5bb35dcc296d4aadff582db2e655b8d39c7470', '2026-06-18 03:24:12.282737+00', NULL, '2026-06-17 11:04:23.096645+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('c96d2e58-ae82-491a-ba0c-233d53b769a1', 'a0000000-0000-0000-0000-000000000001', '574cd734d9583ff6ca99ef191f515a19d92670da28bf4d069886e68c924e5587', '2026-06-18 12:57:26.908106+00', NULL, '2026-06-17 20:57:23.709667+00');
INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at, revoked_at, created_at) VALUES ('d10d030a-7de5-4610-8c04-66195c8aa405', 'a0000000-0000-0000-0000-000000000001', '3fa6afa1a2625c70601908f9be98c656c836be36cb0e192a5aa149def7c5958b', '2026-06-18 13:11:26.933789+00', NULL, '2026-06-16 17:37:27.305805+00');


--
-- Data for Name: buses; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.buses (id, school_id, label, registration_number, active, created_at) VALUES ('22222222-2222-2222-2222-222222222221', '11111111-1111-1111-1111-111111111111', 'Kifaru Bus', 'KCF 912T', true, '2026-06-15 06:14:40.455383+00');
INSERT INTO public.buses (id, school_id, label, registration_number, active, created_at) VALUES ('22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111', 'Ndege Shuttle', 'KDA 556S', true, '2026-06-15 06:14:40.455383+00');
INSERT INTO public.buses (id, school_id, label, registration_number, active, created_at) VALUES ('22222222-2222-2222-2222-222222222223', '11111111-1111-1111-1111-111111111111', 'Ngong', 'KCA 678F', true, '2026-06-15 06:14:40.455383+00');
INSERT INTO public.buses (id, school_id, label, registration_number, active, created_at) VALUES ('22222222-2222-2222-2222-222222222224', '11111111-1111-1111-1111-111111111111', 'Safari Express', 'KCA 201Z', true, '2026-06-15 06:14:40.455383+00');
INSERT INTO public.buses (id, school_id, label, registration_number, active, created_at) VALUES ('22222222-2222-2222-2222-222222222225', '11111111-1111-1111-1111-111111111111', 'Simba Coach', 'KDD 678R', true, '2026-06-15 06:14:40.455383+00');
INSERT INTO public.buses (id, school_id, label, registration_number, active, created_at) VALUES ('22222222-2222-2222-2222-222222222226', '11111111-1111-1111-1111-111111111111', 'Twiga Shuttle', 'KBZ 445P', true, '2026-06-15 06:14:40.455383+00');


--
-- Data for Name: students; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.students (id, school_id, full_name, home_address, home_location_note, active, created_at, grade_level) VALUES ('44444444-4444-4444-4444-444444444441', '11111111-1111-1111-1111-111111111111', 'Faith Achieng', '56 Karen Road', 'Karen Road stop', true, '2026-06-15 06:14:40.457226+00', '2');
INSERT INTO public.students (id, school_id, full_name, home_address, home_location_note, active, created_at, grade_level) VALUES ('44444444-4444-4444-4444-444444444442', '11111111-1111-1111-1111-111111111111', 'Happiness Kenesa', '23 Langata Road', 'Langata Road stop', true, '2026-06-15 06:14:40.457226+00', '5');
INSERT INTO public.students (id, school_id, full_name, home_address, home_location_note, active, created_at, grade_level) VALUES ('44444444-4444-4444-4444-444444444443', '11111111-1111-1111-1111-111111111111', 'James Mwangi', 'Karen road 23', 'Karen road stop', true, '2026-06-15 06:14:40.457226+00', '1');
INSERT INTO public.students (id, school_id, full_name, home_address, home_location_note, active, created_at, grade_level) VALUES ('44444444-4444-4444-4444-444444444444', '11111111-1111-1111-1111-111111111111', 'Michael Otieno', '45 Langata road', 'Langata road stop', true, '2026-06-15 06:14:40.457226+00', '6');
INSERT INTO public.students (id, school_id, full_name, home_address, home_location_note, active, created_at, grade_level) VALUES ('44444444-4444-4444-4444-444444444445', '11111111-1111-1111-1111-111111111111', 'Roy Otieno', '1 Hillcrest Rd', 'Hillcrest Road stop', true, '2026-06-15 06:14:40.457226+00', '5');


--
-- Data for Name: daily_attendance; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: drivers; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.drivers (id, school_id, full_name, phone, default_bus_id, pin_hash, active, created_at) VALUES ('33333333-3333-3333-3333-333333333331', '11111111-1111-1111-1111-111111111111', 'Francis Ochieng', '+254787878000', '22222222-2222-2222-2222-222222222221', 'pbkdf2_sha256$200000$demo-1234-salt$ky4cRYebfgpImLHMweOtPswsvQ/su3GqU49a+lA+Xho=', true, '2026-06-15 06:14:40.456134+00');
INSERT INTO public.drivers (id, school_id, full_name, phone, default_bus_id, pin_hash, active, created_at) VALUES ('33333333-3333-3333-3333-333333333332', '11111111-1111-1111-1111-111111111111', 'Michael Otieno', '+254767890123', '22222222-2222-2222-2222-222222222222', 'pbkdf2_sha256$200000$demo-2468-salt$m+SqOGCoZX+irY5KuSK42kw79Ohl0hytO6fjtli4kI8=', true, '2026-06-15 06:14:40.456134+00');
INSERT INTO public.drivers (id, school_id, full_name, phone, default_bus_id, pin_hash, active, created_at) VALUES ('33333333-3333-3333-3333-333333333333', '11111111-1111-1111-1111-111111111111', 'Frank Nwangi', '+254778787878', '22222222-2222-2222-2222-222222222223', 'pbkdf2_sha256$200000$demo-1357-salt$6bCWB4PuzFo8y5YRXwAgFxUpJ/vDvvCTLb88e72/7PI=', true, '2026-06-15 06:14:40.456134+00');
INSERT INTO public.drivers (id, school_id, full_name, phone, default_bus_id, pin_hash, active, created_at) VALUES ('33333333-3333-3333-3333-333333333334', '11111111-1111-1111-1111-111111111111', 'James Mwangi', '+254712345678', '22222222-2222-2222-2222-222222222224', 'pbkdf2_sha256$200000$demo-8642-salt$wlYTzH5jNYPsHoPnMKSJHrYUXEsw3Y15gO0tOTrkmFE=', true, '2026-06-15 06:14:40.456134+00');
INSERT INTO public.drivers (id, school_id, full_name, phone, default_bus_id, pin_hash, active, created_at) VALUES ('33333333-3333-3333-3333-333333333335', '11111111-1111-1111-1111-111111111111', 'David Kamau', '+254734567890', '22222222-2222-2222-2222-222222222225', 'pbkdf2_sha256$200000$demo-9753-salt$2tyiuNgOorHmR04JEdt3JkTFsU4v9+vrjtrWf1gaFkg=', true, '2026-06-15 06:14:40.456134+00');
INSERT INTO public.drivers (id, school_id, full_name, phone, default_bus_id, pin_hash, active, created_at) VALUES ('33333333-3333-3333-3333-333333333336', '11111111-1111-1111-1111-111111111111', 'Peter Ochieng', '+254723456789', '22222222-2222-2222-2222-222222222226', 'pbkdf2_sha256$200000$demo-1122-salt$4ZlBZjYqN1VDlwad8nBc3m7d6wORNs/3pnnw3318jvU=', true, '2026-06-15 06:14:40.456134+00');


--
-- Data for Name: driver_sessions; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: live_buses; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.live_buses (id, name, plate_number, driver_id, driver_name, driver_phone, capacity, status, current_lat, current_lng, created_at) VALUES ('dace7cf0-b277-440f-85fb-9efd9fb97fa6', 'Twiga', 'KDA 103C', 'a0000000-0000-0000-0000-000000000004', 'Francis Ochieng', '+254700000004', 45, 'idle', NULL, NULL, '2026-06-15 15:00:06.99578+00');
INSERT INTO public.live_buses (id, name, plate_number, driver_id, driver_name, driver_phone, capacity, status, current_lat, current_lng, created_at) VALUES ('3e8039fe-a5df-425b-9c82-1c70729efb71', 'Mamba', 'KDA 104A', 'a0000000-0000-0000-0000-000000000005', 'Mary Wanjiru', '+254700000005', 45, 'idle', NULL, NULL, '2026-06-15 15:00:30.322133+00');
INSERT INTO public.live_buses (id, name, plate_number, driver_id, driver_name, driver_phone, capacity, status, current_lat, current_lng, created_at) VALUES ('146a1837-af5e-494c-8be6-f78db9c4280a', 'Simba', 'KDA 102B', 'a0000000-0000-0000-0000-000000000003', 'Daniel Kamau', '+254700000003', 45, 'idle', NULL, NULL, '2026-06-15 14:59:41.815535+00');


--
-- Data for Name: live_fcm_tokens; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: live_schools; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.live_schools (id, name, address, phone, lat, lng, created_at) VALUES ('5cae0000-0000-0000-0000-000000000001', 'Greenfield Academy', 'Hillcrest road, Nairobi', '+254709000000', -1.333667, 36.73547, '2026-06-15 06:14:40.563623+00');


--
-- Data for Name: live_routes; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.live_routes (id, name, type, bus_id, school_id, created_at) VALUES ('40000000-0000-0000-0000-000000000002', 'Express 1 — Afternoon', 'afternoon', '146a1837-af5e-494c-8be6-f78db9c4280a', '5cae0000-0000-0000-0000-000000000001', '2026-06-15 06:14:40.564324+00');
INSERT INTO public.live_routes (id, name, type, bus_id, school_id, created_at) VALUES ('40000000-0000-0000-0000-000000000001', 'Express 1 — Morning', 'morning', '146a1837-af5e-494c-8be6-f78db9c4280a', '5cae0000-0000-0000-0000-000000000001', '2026-06-15 06:14:40.564324+00');
INSERT INTO public.live_routes (id, name, type, bus_id, school_id, created_at) VALUES ('40000000-0000-0000-0000-000000000003', 'Express 2 — Morning', 'morning', '3e8039fe-a5df-425b-9c82-1c70729efb71', '5cae0000-0000-0000-0000-000000000001', '2026-06-15 06:14:40.564324+00');
INSERT INTO public.live_routes (id, name, type, bus_id, school_id, created_at) VALUES ('fe6b8d54-6b87-4875-a5f8-bd9025ccb75b', 'Express 2 - Afternoon', 'afternoon', '3e8039fe-a5df-425b-9c82-1c70729efb71', '5cae0000-0000-0000-0000-000000000001', '2026-06-15 15:01:33.708442+00');


--
-- Data for Name: live_runs; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.live_runs (id, bus_id, route_id, school_id, driver_id, type, date, start_time, end_time, status, total_stops, stops_completed, total_students, students_boarded, incidents, created_at) VALUES ('60000000-0000-0000-0000-000000000001', NULL, '40000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000003', 'morning', '2026-06-14', '06:35', '07:12', 'completed', 4, 4, 4, 4, 0, '2026-06-15 06:14:40.566643+00');
INSERT INTO public.live_runs (id, bus_id, route_id, school_id, driver_id, type, date, start_time, end_time, status, total_stops, stops_completed, total_students, students_boarded, incidents, created_at) VALUES ('60000000-0000-0000-0000-000000000002', NULL, '40000000-0000-0000-0000-000000000003', '5cae0000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000005', 'morning', '2026-06-15', '06:30', '07:20', 'completed', 3, 3, 3, 3, 1, '2026-06-15 06:14:40.566643+00');
INSERT INTO public.live_runs (id, bus_id, route_id, school_id, driver_id, type, date, start_time, end_time, status, total_stops, stops_completed, total_students, students_boarded, incidents, created_at) VALUES ('c07b1972-b1c0-48e2-a47b-f268340863f0', '146a1837-af5e-494c-8be6-f78db9c4280a', '40000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000003', 'morning', '2026-06-15', '18:19', '18:19', 'completed', 4, 4, 3, 0, 0, '2026-06-15 15:19:09.806628+00');
INSERT INTO public.live_runs (id, bus_id, route_id, school_id, driver_id, type, date, start_time, end_time, status, total_stops, stops_completed, total_students, students_boarded, incidents, created_at) VALUES ('c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', '146a1837-af5e-494c-8be6-f78db9c4280a', '40000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000003', 'morning', '2026-06-16', '23:07', '23:07', 'completed', 4, 4, 3, 0, 1, '2026-06-16 20:07:24.48809+00');
INSERT INTO public.live_runs (id, bus_id, route_id, school_id, driver_id, type, date, start_time, end_time, status, total_stops, stops_completed, total_students, students_boarded, incidents, created_at) VALUES ('54bd5a88-a1f4-4483-9e55-25140db7d219', '146a1837-af5e-494c-8be6-f78db9c4280a', '40000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000003', 'morning', '2026-06-16', '23:22', NULL, 'completed', 4, 1, 3, 0, 0, '2026-06-16 20:22:02.150419+00');
INSERT INTO public.live_runs (id, bus_id, route_id, school_id, driver_id, type, date, start_time, end_time, status, total_stops, stops_completed, total_students, students_boarded, incidents, created_at) VALUES ('6b6cba81-a13f-42ca-aefb-f2c8a4a2e994', '146a1837-af5e-494c-8be6-f78db9c4280a', '40000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000003', 'morning', '2026-06-16', '23:45', '23:45', 'completed', 4, 4, 3, 0, 0, '2026-06-16 20:45:09.012828+00');
INSERT INTO public.live_runs (id, bus_id, route_id, school_id, driver_id, type, date, start_time, end_time, status, total_stops, stops_completed, total_students, students_boarded, incidents, created_at) VALUES ('2e796f4f-4ed2-4b3a-8066-7f9985c950a7', '146a1837-af5e-494c-8be6-f78db9c4280a', '40000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000003', 'morning', '2026-06-16', '23:47', NULL, 'in-progress', 4, 1, 3, 0, 0, '2026-06-16 20:47:05.201034+00');
INSERT INTO public.live_runs (id, bus_id, route_id, school_id, driver_id, type, date, start_time, end_time, status, total_stops, stops_completed, total_students, students_boarded, incidents, created_at) VALUES ('967d7061-d005-4af2-b255-3a31f565fc8c', '146a1837-af5e-494c-8be6-f78db9c4280a', '40000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000003', 'morning', '2026-06-17', '13:49', '13:51', 'completed', 4, 4, 3, 0, 0, '2026-06-17 10:49:50.019135+00');
INSERT INTO public.live_runs (id, bus_id, route_id, school_id, driver_id, type, date, start_time, end_time, status, total_stops, stops_completed, total_students, students_boarded, incidents, created_at) VALUES ('c78b07bb-d18e-4caa-a21c-976bdc489743', '146a1837-af5e-494c-8be6-f78db9c4280a', '40000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000003', 'morning', '2026-06-17', '13:51', '13:52', 'completed', 4, 4, 3, 0, 1, '2026-06-17 10:51:06.739143+00');


--
-- Data for Name: live_incidents; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.live_incidents (id, run_id, driver_id, driver_name, bus_id, bus_name, type, description, acknowledged, acknowledged_at, acknowledged_by, created_at) VALUES ('70000000-0000-0000-0000-000000000001', NULL, 'a0000000-0000-0000-0000-000000000003', 'Daniel Kamau', NULL, 'Express 1', 'traffic', 'Heavy traffic on Ngong Road, approximately 15 minute delay.', false, NULL, NULL, '2026-06-15 06:06:40.567316+00');
INSERT INTO public.live_incidents (id, run_id, driver_id, driver_name, bus_id, bus_name, type, description, acknowledged, acknowledged_at, acknowledged_by, created_at) VALUES ('70000000-0000-0000-0000-000000000002', NULL, 'a0000000-0000-0000-0000-000000000003', 'Daniel Kamau', NULL, 'Express 1', 'arrival', 'Express 1 has arrived at Greenfield Academy.', false, NULL, NULL, '2026-06-15 05:54:40.567316+00');
INSERT INTO public.live_incidents (id, run_id, driver_id, driver_name, bus_id, bus_name, type, description, acknowledged, acknowledged_at, acknowledged_by, created_at) VALUES ('70000000-0000-0000-0000-000000000005', NULL, 'a0000000-0000-0000-0000-000000000003', 'Daniel Kamau', NULL, 'Express 1', 'student', 'A student left a bag at the stop; returning briefly.', false, NULL, NULL, '2026-06-15 05:32:40.567316+00');
INSERT INTO public.live_incidents (id, run_id, driver_id, driver_name, bus_id, bus_name, type, description, acknowledged, acknowledged_at, acknowledged_by, created_at) VALUES ('70000000-0000-0000-0000-000000000003', NULL, 'a0000000-0000-0000-0000-000000000005', 'Mary Wanjiru', NULL, 'Express 2', 'arrival', 'Express 2 has arrived at Greenfield Academy.', false, NULL, NULL, '2026-06-15 05:49:40.567316+00');
INSERT INTO public.live_incidents (id, run_id, driver_id, driver_name, bus_id, bus_name, type, description, acknowledged, acknowledged_at, acknowledged_by, created_at) VALUES ('70000000-0000-0000-0000-000000000007', NULL, 'a0000000-0000-0000-0000-000000000005', 'Mary Wanjiru', NULL, 'Express 2', 'other', 'Route adjusted due to a road closure on Argwings Kodhek.', false, NULL, NULL, '2026-06-15 05:04:40.567316+00');
INSERT INTO public.live_incidents (id, run_id, driver_id, driver_name, bus_id, bus_name, type, description, acknowledged, acknowledged_at, acknowledged_by, created_at) VALUES ('70000000-0000-0000-0000-000000000004', NULL, 'a0000000-0000-0000-0000-000000000004', 'Francis Ochieng', NULL, 'Express 3', 'accident', 'Minor road accident reported near Yaya Centre. All students safe.', false, NULL, NULL, '2026-06-15 05:39:40.567316+00');
INSERT INTO public.live_incidents (id, run_id, driver_id, driver_name, bus_id, bus_name, type, description, acknowledged, acknowledged_at, acknowledged_by, created_at) VALUES ('70000000-0000-0000-0000-000000000006', NULL, NULL, 'John Otieno', NULL, 'Shuttle A', 'breakdown', 'Engine warning light on; pulling over to inspect.', false, NULL, NULL, '2026-06-15 05:19:40.567316+00');
INSERT INTO public.live_incidents (id, run_id, driver_id, driver_name, bus_id, bus_name, type, description, acknowledged, acknowledged_at, acknowledged_by, created_at) VALUES ('09b68e72-79fd-46e5-8b3d-5db583ff481d', NULL, 'a0000000-0000-0000-0000-000000000003', 'Daniel Kamau', '146a1837-af5e-494c-8be6-f78db9c4280a', 'Simba', 'breakdown', 'IT incident 4f13fa', false, NULL, NULL, '2026-06-15 15:19:09.824988+00');
INSERT INTO public.live_incidents (id, run_id, driver_id, driver_name, bus_id, bus_name, type, description, acknowledged, acknowledged_at, acknowledged_by, created_at) VALUES ('54c6f21b-f895-4476-8db2-4b329b63a25e', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', 'a0000000-0000-0000-0000-000000000003', 'Daniel Kamau', '146a1837-af5e-494c-8be6-f78db9c4280a', 'Simba', 'arrival', 'Simba has arrived at Greenfield Academy.', true, '2026-06-16 20:08:13.454287+00', 'a0000000-0000-0000-0000-000000000001', '2026-06-16 20:07:52.043346+00');
INSERT INTO public.live_incidents (id, run_id, driver_id, driver_name, bus_id, bus_name, type, description, acknowledged, acknowledged_at, acknowledged_by, created_at) VALUES ('b470c37f-3f76-4a65-8f10-79827d5378f9', 'c78b07bb-d18e-4caa-a21c-976bdc489743', 'a0000000-0000-0000-0000-000000000003', 'Daniel Kamau', '146a1837-af5e-494c-8be6-f78db9c4280a', 'Simba', 'arrival', 'Simba has arrived at Greenfield Academy.', false, NULL, NULL, '2026-06-17 10:52:37.980864+00');


--
-- Data for Name: live_students; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.live_students (id, name, grade, parent_name, parent_phone, parent_phone2, parent_email, home_address, home_lat, home_lng, pickup_time, status, bus_id, school_id, boarding_stop_name, created_at) VALUES ('50000000-0000-0000-0000-000000000001', 'Faith Achieng', 'Grade 4', 'Amina Achieng', '+254700000002', NULL, 'and7005@gmail.com', 'Kilimani, Nairobi', -1.2902, 36.7823, '06:40', 'at-school', '146a1837-af5e-494c-8be6-f78db9c4280a', '5cae0000-0000-0000-0000-000000000001', 'Kilimani Stop', '2026-06-15 06:14:40.564728+00');
INSERT INTO public.live_students (id, name, grade, parent_name, parent_phone, parent_phone2, parent_email, home_address, home_lat, home_lng, pickup_time, status, bus_id, school_id, boarding_stop_name, created_at) VALUES ('50000000-0000-0000-0000-000000000003', 'Happiness Kenesa', 'Grade 5', 'Joseph Kenesa', '+254700000010', NULL, 'kenesa@example.com', 'Lavington, Nairobi', -1.2789, 36.7685, '06:48', 'at-school', '146a1837-af5e-494c-8be6-f78db9c4280a', '5cae0000-0000-0000-0000-000000000001', 'Lavington Stop', '2026-06-15 06:14:40.564728+00');
INSERT INTO public.live_students (id, name, grade, parent_name, parent_phone, parent_phone2, parent_email, home_address, home_lat, home_lng, pickup_time, status, bus_id, school_id, boarding_stop_name, created_at) VALUES ('50000000-0000-0000-0000-000000000004', 'Kevin Mwangi', 'Grade 3', 'Lucy Mwangi', '+254700000011', NULL, 'lucy@example.com', 'Karen, Nairobi', -1.3283746, 36.7049676, '06:55', 'at-school', '146a1837-af5e-494c-8be6-f78db9c4280a', '5cae0000-0000-0000-0000-000000000001', NULL, '2026-06-15 06:14:40.564728+00');


--
-- Data for Name: live_notifications; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('79c723a3-3969-4c6f-9ef1-582aed9157d3', '25f9bc97-1446-4487-9633-2756b5ba0ba0', '50000000-0000-0000-0000-000000000004', 'c07b1972-b1c0-48e2-a47b-f268340863f0', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Kevin Mwangi ready.', false, '2026-06-15 15:19:09.811771+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('0bb9585a-486e-4d29-8437-9d6d4689dfad', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', '50000000-0000-0000-0000-000000000003', 'c07b1972-b1c0-48e2-a47b-f268340863f0', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Happiness Kenesa ready.', false, '2026-06-15 15:19:09.812421+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('add884af-0e52-423b-bd1b-6530cdea5432', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', '50000000-0000-0000-0000-000000000003', NULL, '146a1837-af5e-494c-8be6-f78db9c4280a', 'incident', 'Vehicle breakdown', 'IT incident 4f13fa', false, '2026-06-15 15:19:09.827288+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('a934db0b-6462-49ef-a560-e49cc4ddaaf8', '25f9bc97-1446-4487-9633-2756b5ba0ba0', '50000000-0000-0000-0000-000000000004', NULL, '146a1837-af5e-494c-8be6-f78db9c4280a', 'incident', 'Vehicle breakdown', 'IT incident 4f13fa', false, '2026-06-15 15:19:09.827834+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('9ae3861e-4245-4939-ad27-5e981d5d22a1', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000001', 'c07b1972-b1c0-48e2-a47b-f268340863f0', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Faith Achieng ready.', true, '2026-06-15 15:19:09.811035+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('9e65d927-2575-4d70-b1ef-99faf12b6bb0', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000001', NULL, '146a1837-af5e-494c-8be6-f78db9c4280a', 'incident', 'Vehicle breakdown', 'IT incident 4f13fa', true, '2026-06-15 15:19:09.82652+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('aeacc126-66fe-4ad5-92a4-2993ddb5d333', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', '50000000-0000-0000-0000-000000000003', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Happiness Kenesa ready.', false, '2026-06-16 20:07:24.49831+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('ed2ba0b6-ffa7-45e3-83f2-b10e9bbd23ce', '25f9bc97-1446-4487-9633-2756b5ba0ba0', '50000000-0000-0000-0000-000000000004', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Kevin Mwangi ready.', false, '2026-06-16 20:07:24.499932+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('ea0c372c-db89-4312-96a7-b8feb8607502', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', '50000000-0000-0000-0000-000000000003', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', '146a1837-af5e-494c-8be6-f78db9c4280a', 'student-boarded', 'Boarded the bus', 'Happiness Kenesa has boarded Simba.', false, '2026-06-16 20:07:40.700266+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('f48b65c0-7060-45af-9bbd-52348660d356', '25f9bc97-1446-4487-9633-2756b5ba0ba0', '50000000-0000-0000-0000-000000000004', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', '146a1837-af5e-494c-8be6-f78db9c4280a', 'student-boarded', 'Boarded the bus', 'Kevin Mwangi has boarded Simba.', false, '2026-06-16 20:07:44.281365+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('348300ac-5b6c-42de-960d-4e8a1ed39d41', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', '50000000-0000-0000-0000-000000000003', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', '146a1837-af5e-494c-8be6-f78db9c4280a', 'reached-school', 'Arrived at school', 'Happiness Kenesa has reached school safely.', false, '2026-06-16 20:07:52.050534+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('b436ef18-1fdb-4d6a-b76a-8ebd857bdf53', '25f9bc97-1446-4487-9633-2756b5ba0ba0', '50000000-0000-0000-0000-000000000004', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', '146a1837-af5e-494c-8be6-f78db9c4280a', 'reached-school', 'Arrived at school', 'Kevin Mwangi has reached school safely.', false, '2026-06-16 20:07:52.051284+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('26c7c710-6083-4527-8c50-2dd9b2dabd65', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', '50000000-0000-0000-0000-000000000003', '54bd5a88-a1f4-4483-9e55-25140db7d219', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Happiness Kenesa ready.', false, '2026-06-16 20:22:02.162873+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('73accfd8-5ded-4172-a167-2769a4c32d5e', '25f9bc97-1446-4487-9633-2756b5ba0ba0', '50000000-0000-0000-0000-000000000004', '54bd5a88-a1f4-4483-9e55-25140db7d219', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Kevin Mwangi ready.', false, '2026-06-16 20:22:02.164859+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('b6b37199-4de4-42b7-8507-6a42c57e6b6c', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', '50000000-0000-0000-0000-000000000003', '967d7061-d005-4af2-b255-3a31f565fc8c', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Happiness Kenesa ready.', false, '2026-06-17 10:49:50.064836+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('96c68e17-aa28-4ca4-a6cd-8b2226d7ae8a', '25f9bc97-1446-4487-9633-2756b5ba0ba0', '50000000-0000-0000-0000-000000000004', '967d7061-d005-4af2-b255-3a31f565fc8c', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Kevin Mwangi ready.', false, '2026-06-17 10:49:50.06614+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('cef4ec4d-18f5-471c-8a30-7c7655f3bf9d', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', '50000000-0000-0000-0000-000000000003', '967d7061-d005-4af2-b255-3a31f565fc8c', '146a1837-af5e-494c-8be6-f78db9c4280a', 'bus-approaching', 'Bus approaching', 'Simba is approaching Happiness Kenesa''s stop — it''s the next stop.', false, '2026-06-17 10:49:57.631703+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('c2b74ce9-2175-49ab-a018-335cbb937208', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', '50000000-0000-0000-0000-000000000003', 'c78b07bb-d18e-4caa-a21c-976bdc489743', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Happiness Kenesa ready.', false, '2026-06-17 10:51:06.747984+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('704777cd-0f92-4935-bee5-89a985e4109f', '25f9bc97-1446-4487-9633-2756b5ba0ba0', '50000000-0000-0000-0000-000000000004', 'c78b07bb-d18e-4caa-a21c-976bdc489743', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Kevin Mwangi ready.', false, '2026-06-17 10:51:06.749834+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('42872172-54f9-4059-b231-fac38378fd5a', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', '50000000-0000-0000-0000-000000000003', 'c78b07bb-d18e-4caa-a21c-976bdc489743', '146a1837-af5e-494c-8be6-f78db9c4280a', 'bus-approaching', 'Bus approaching', 'Simba is approaching Happiness Kenesa''s stop — it''s the next stop.', false, '2026-06-17 10:51:08.75983+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('108f186a-b7a0-4e32-85ae-877c28b91a99', '25f9bc97-1446-4487-9633-2756b5ba0ba0', '50000000-0000-0000-0000-000000000004', 'c78b07bb-d18e-4caa-a21c-976bdc489743', '146a1837-af5e-494c-8be6-f78db9c4280a', 'bus-approaching', 'Bus approaching', 'Simba is approaching Kevin Mwangi''s stop — it''s the next stop.', false, '2026-06-17 10:51:25.10605+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('ca62a8dd-b8e8-4b3a-816a-a47e65297da3', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000001', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Faith Achieng ready.', true, '2026-06-16 20:07:24.496398+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('e6e57385-16c2-40e2-8cb4-2fe6e11afd97', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000001', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', '146a1837-af5e-494c-8be6-f78db9c4280a', 'student-boarded', 'Boarded the bus', 'Faith Achieng has boarded Simba.', true, '2026-06-16 20:07:39.577597+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('b51c3457-3aad-4f74-a240-4043f7b27e61', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000001', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', '146a1837-af5e-494c-8be6-f78db9c4280a', 'reached-school', 'Arrived at school', 'Faith Achieng has reached school safely.', true, '2026-06-16 20:07:52.049495+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('db9bbc21-a788-4685-a32f-d3b41c7ce85b', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000001', '54bd5a88-a1f4-4483-9e55-25140db7d219', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Faith Achieng ready.', true, '2026-06-16 20:22:02.160987+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('dca7cd4e-fcec-4c0b-b611-de60a765c443', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000001', '967d7061-d005-4af2-b255-3a31f565fc8c', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Faith Achieng ready.', true, '2026-06-17 10:49:50.061203+00');
INSERT INTO public.live_notifications (id, user_id, student_id, run_id, bus_id, type, title, body, read, created_at) VALUES ('b2dd55dd-adbc-4c10-9cc8-2943cd4c75e1', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000001', 'c78b07bb-d18e-4caa-a21c-976bdc489743', '146a1837-af5e-494c-8be6-f78db9c4280a', 'run-started', 'Bus on the way', 'Simba has started the morning pickup run. Get Faith Achieng ready.', true, '2026-06-17 10:51:06.747034+00');


--
-- Data for Name: live_parent_students; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.live_parent_students (id, parent_id, student_id) VALUES ('52000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000001');
INSERT INTO public.live_parent_students (id, parent_id, student_id) VALUES ('3c55e8b6-a72d-497a-8dbc-56f34bcb7db8', 'd94fa87f-f565-46e1-a1c2-35fa5e14a221', '50000000-0000-0000-0000-000000000003');
INSERT INTO public.live_parent_students (id, parent_id, student_id) VALUES ('ba52e3a3-be6d-4bbc-b121-8d4be2563761', '25f9bc97-1446-4487-9633-2756b5ba0ba0', '50000000-0000-0000-0000-000000000004');


--
-- Data for Name: live_push_subscriptions; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: live_route_stops; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.live_route_stops (id, route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('91de6926-3465-4d2e-a327-1e6555ff5819', '40000000-0000-0000-0000-000000000002', 'Kilimani, Nairobi', 3, '06:40', -1.2902, 36.7823, false, '50000000-0000-0000-0000-000000000001');
INSERT INTO public.live_route_stops (id, route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('d82c5a4c-c5ec-483b-a5dd-e9f71e12faca', '40000000-0000-0000-0000-000000000002', 'Lavington, Nairobi', 2, '06:48', -1.2789, 36.7685, false, '50000000-0000-0000-0000-000000000003');
INSERT INTO public.live_route_stops (id, route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('30f03a8d-8536-43e8-a852-df2d4ef5ec8f', '40000000-0000-0000-0000-000000000002', 'Greenfield Academy', 1, NULL, -1.333667, 36.73547, true, NULL);
INSERT INTO public.live_route_stops (id, route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('0f1ffe90-2324-4636-a281-5e09d9794a9b', '40000000-0000-0000-0000-000000000001', 'Kilimani, Nairobi', 1, '06:40', -1.2902, 36.7823, false, '50000000-0000-0000-0000-000000000001');
INSERT INTO public.live_route_stops (id, route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('1af0217a-75e2-493e-a260-dd9bda09b73c', '40000000-0000-0000-0000-000000000001', 'Lavington, Nairobi', 2, '06:48', -1.2789, 36.7685, false, '50000000-0000-0000-0000-000000000003');
INSERT INTO public.live_route_stops (id, route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('1876a4ec-c283-4373-ae31-79a8acd68128', '40000000-0000-0000-0000-000000000001', 'Karen, Nairobi', 3, '06:55', -1.3283746, 36.7049676, false, '50000000-0000-0000-0000-000000000004');
INSERT INTO public.live_route_stops (id, route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('0b773abb-c036-409f-a3c3-c01ee737b699', '40000000-0000-0000-0000-000000000001', 'Greenfield Academy', 4, NULL, -1.333667, 36.73547, true, NULL);
INSERT INTO public.live_route_stops (id, route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('d4b3e40d-b4c2-4885-b1e9-be841468ddfa', '40000000-0000-0000-0000-000000000003', 'Greenfield Academy', 1, NULL, -1.333667, 36.73547, true, NULL);
INSERT INTO public.live_route_stops (id, route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('e4a46921-5d31-431d-bba7-73e75f417132', 'fe6b8d54-6b87-4875-a5f8-bd9025ccb75b', 'Karen, Nairobi', 2, '06:55', -1.3283746, 36.7049676, false, '50000000-0000-0000-0000-000000000004');
INSERT INTO public.live_route_stops (id, route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('4838db60-06e0-4cd9-9ea3-63d3f4edf6ab', 'fe6b8d54-6b87-4875-a5f8-bd9025ccb75b', 'Greenfield Academy', 1, NULL, -1.333667, 36.73547, true, NULL);


--
-- Data for Name: live_student_absences; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: live_student_routes; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.live_student_routes (id, student_id, route_id) VALUES ('f32f9e41-d729-4a14-a021-7e48b44dc654', '50000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000002');
INSERT INTO public.live_student_routes (id, student_id, route_id) VALUES ('ffab373c-7f36-4dfd-9639-29f24e8f291e', '50000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000001');
INSERT INTO public.live_student_routes (id, student_id, route_id) VALUES ('9e90442b-3647-488f-be3d-0d5a1fb284eb', '50000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000001');
INSERT INTO public.live_student_routes (id, student_id, route_id) VALUES ('ef77142e-2983-4f87-a034-4b01de15ad87', '50000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000002');
INSERT INTO public.live_student_routes (id, student_id, route_id) VALUES ('623de08f-215f-44ef-9304-28e01ec7b394', '50000000-0000-0000-0000-000000000004', 'fe6b8d54-6b87-4875-a5f8-bd9025ccb75b');
INSERT INTO public.live_student_routes (id, student_id, route_id) VALUES ('8bd5e409-8ca3-4b0d-bb5a-a91117edb4f5', '50000000-0000-0000-0000-000000000004', '40000000-0000-0000-0000-000000000001');


--
-- Data for Name: parent_links; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.parent_links (id, school_id, student_id, token, revoked_at, created_at) VALUES ('66666666-6666-6666-6666-666666666661', '11111111-1111-1111-1111-111111111111', '44444444-4444-4444-4444-444444444441', 'demo-parent-token-00000000000000000001', NULL, '2026-06-15 06:14:40.458254+00');


--
-- Data for Name: push_subscriptions; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: staff_passengers; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: trips; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.trips (id, school_id, bus_id, driver_id, name, session, service_date, scheduled_start, status, started_at, ended_at, created_at) VALUES ('77777777-7777-7777-7777-777777777771', '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222221', '33333333-3333-3333-3333-333333333331', 'Express 2 (AM)', 'morning', '2026-06-15', '07:00:00', 'scheduled', NULL, NULL, '2026-06-15 06:14:40.458637+00');
INSERT INTO public.trips (id, school_id, bus_id, driver_id, name, session, service_date, scheduled_start, status, started_at, ended_at, created_at) VALUES ('77777777-7777-7777-7777-777777777772', '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222221', '33333333-3333-3333-3333-333333333331', 'Express 2 Return (PM)', 'afternoon', '2026-06-15', '15:30:00', 'scheduled', NULL, NULL, '2026-06-15 06:14:40.458637+00');
INSERT INTO public.trips (id, school_id, bus_id, driver_id, name, session, service_date, scheduled_start, status, started_at, ended_at, created_at) VALUES ('77777777-7777-7777-7777-777777777773', '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222226', '33333333-3333-3333-3333-333333333336', 'Express 1 (AM)', 'morning', '2026-06-15', '07:00:00', 'scheduled', NULL, NULL, '2026-06-15 06:14:40.458637+00');
INSERT INTO public.trips (id, school_id, bus_id, driver_id, name, session, service_date, scheduled_start, status, started_at, ended_at, created_at) VALUES ('77777777-7777-7777-7777-777777777774', '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222226', '33333333-3333-3333-3333-333333333336', 'Express 1 Return (PM)', 'afternoon', '2026-06-15', '15:30:00', 'scheduled', NULL, NULL, '2026-06-15 06:14:40.458637+00');


--
-- Data for Name: trip_passengers; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.trip_passengers (id, school_id, trip_id, passenger_type, student_id, staff_passenger_id, sequence_position, estimated_minutes_from_start, actual_pickup_time, actual_dropoff_time, status, created_at) VALUES ('88888888-8888-8888-8888-888888888881', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777771', 'student', '44444444-4444-4444-4444-444444444443', NULL, 1, 45, NULL, NULL, 'pending', '2026-06-15 06:14:40.459417+00');
INSERT INTO public.trip_passengers (id, school_id, trip_id, passenger_type, student_id, staff_passenger_id, sequence_position, estimated_minutes_from_start, actual_pickup_time, actual_dropoff_time, status, created_at) VALUES ('88888888-8888-8888-8888-888888888882', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777771', 'student', '44444444-4444-4444-4444-444444444441', NULL, 2, 50, NULL, NULL, 'pending', '2026-06-15 06:14:40.459417+00');
INSERT INTO public.trip_passengers (id, school_id, trip_id, passenger_type, student_id, staff_passenger_id, sequence_position, estimated_minutes_from_start, actual_pickup_time, actual_dropoff_time, status, created_at) VALUES ('88888888-8888-8888-8888-888888888883', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777772', 'student', '44444444-4444-4444-4444-444444444443', NULL, 1, 10, NULL, NULL, 'pending', '2026-06-15 06:14:40.459417+00');
INSERT INTO public.trip_passengers (id, school_id, trip_id, passenger_type, student_id, staff_passenger_id, sequence_position, estimated_minutes_from_start, actual_pickup_time, actual_dropoff_time, status, created_at) VALUES ('88888888-8888-8888-8888-888888888884', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777772', 'student', '44444444-4444-4444-4444-444444444441', NULL, 2, 20, NULL, NULL, 'pending', '2026-06-15 06:14:40.459417+00');
INSERT INTO public.trip_passengers (id, school_id, trip_id, passenger_type, student_id, staff_passenger_id, sequence_position, estimated_minutes_from_start, actual_pickup_time, actual_dropoff_time, status, created_at) VALUES ('88888888-8888-8888-8888-888888888885', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777773', 'student', '44444444-4444-4444-4444-444444444442', NULL, 1, 5, NULL, NULL, 'pending', '2026-06-15 06:14:40.459417+00');
INSERT INTO public.trip_passengers (id, school_id, trip_id, passenger_type, student_id, staff_passenger_id, sequence_position, estimated_minutes_from_start, actual_pickup_time, actual_dropoff_time, status, created_at) VALUES ('88888888-8888-8888-8888-888888888886', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777773', 'student', '44444444-4444-4444-4444-444444444444', NULL, 2, 10, NULL, NULL, 'pending', '2026-06-15 06:14:40.459417+00');
INSERT INTO public.trip_passengers (id, school_id, trip_id, passenger_type, student_id, staff_passenger_id, sequence_position, estimated_minutes_from_start, actual_pickup_time, actual_dropoff_time, status, created_at) VALUES ('88888888-8888-8888-8888-888888888887', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777773', 'student', '44444444-4444-4444-4444-444444444445', NULL, 3, 15, NULL, NULL, 'pending', '2026-06-15 06:14:40.459417+00');
INSERT INTO public.trip_passengers (id, school_id, trip_id, passenger_type, student_id, staff_passenger_id, sequence_position, estimated_minutes_from_start, actual_pickup_time, actual_dropoff_time, status, created_at) VALUES ('88888888-8888-8888-8888-888888888888', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777774', 'student', '44444444-4444-4444-4444-444444444442', NULL, 1, 5, NULL, NULL, 'pending', '2026-06-15 06:14:40.459417+00');
INSERT INTO public.trip_passengers (id, school_id, trip_id, passenger_type, student_id, staff_passenger_id, sequence_position, estimated_minutes_from_start, actual_pickup_time, actual_dropoff_time, status, created_at) VALUES ('88888888-8888-8888-8888-888888888889', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777774', 'student', '44444444-4444-4444-4444-444444444444', NULL, 2, 10, NULL, NULL, 'pending', '2026-06-15 06:14:40.459417+00');
INSERT INTO public.trip_passengers (id, school_id, trip_id, passenger_type, student_id, staff_passenger_id, sequence_position, estimated_minutes_from_start, actual_pickup_time, actual_dropoff_time, status, created_at) VALUES ('88888888-8888-8888-8888-888888888890', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777774', 'student', '44444444-4444-4444-4444-444444444445', NULL, 3, 15, NULL, NULL, 'pending', '2026-06-15 06:14:40.459417+00');


--
-- Data for Name: trip_events; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.trip_events (id, school_id, trip_id, trip_passenger_id, event_type, created_by_role, created_by_id, occurred_at, metadata) VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777771', NULL, 'issue_reported', 'driver', '33333333-3333-3333-3333-333333333331', '2026-06-03 06:19:40.460077+00', '{"badge": "New", "title": "Heavy Traffic / Delay", "message": "Heavy traffic on Hillcrest Road, we will delay by 5 minutes", "admin_alert": "true"}');
INSERT INTO public.trip_events (id, school_id, trip_id, trip_passenger_id, event_type, created_by_role, created_by_id, occurred_at, metadata) VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777771', NULL, 'issue_reported', 'driver', '33333333-3333-3333-3333-333333333331', '2026-06-03 06:14:40.460077+00', '{"badge": "New", "title": "arrival", "message": "Kifaru Bus has arrived at School Gates.", "admin_alert": "true"}');
INSERT INTO public.trip_events (id, school_id, trip_id, trip_passenger_id, event_type, created_by_role, created_by_id, occurred_at, metadata) VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777771', NULL, 'issue_reported', 'driver', '33333333-3333-3333-3333-333333333331', '2026-05-17 06:14:40.460077+00', '{"badge": "New", "title": "arrival", "message": "Kifaru Bus has arrived at School Gates.", "admin_alert": "true"}');
INSERT INTO public.trip_events (id, school_id, trip_id, trip_passenger_id, event_type, created_by_role, created_by_id, occurred_at, metadata) VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa4', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777771', NULL, 'issue_reported', 'driver', '33333333-3333-3333-3333-333333333331', '2026-05-17 06:22:40.460077+00', '{"badge": "New", "title": "arrival", "message": "Kifaru Bus has arrived at School Gates.", "admin_alert": "true"}');
INSERT INTO public.trip_events (id, school_id, trip_id, trip_passenger_id, event_type, created_by_role, created_by_id, occurred_at, metadata) VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa5', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777771', NULL, 'issue_reported', 'driver', '33333333-3333-3333-3333-333333333331', '2026-05-10 06:14:40.460077+00', '{"badge": "New", "title": "arrival", "message": "Kifaru Bus has arrived at School Gates.", "admin_alert": "true"}');
INSERT INTO public.trip_events (id, school_id, trip_id, trip_passenger_id, event_type, created_by_role, created_by_id, occurred_at, metadata) VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa6', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777771', NULL, 'issue_reported', 'driver', '33333333-3333-3333-3333-333333333331', '2026-05-10 06:25:40.460077+00', '{"badge": "New", "title": "arrival", "message": "Kifaru Bus has arrived at School Gates.", "admin_alert": "true"}');
INSERT INTO public.trip_events (id, school_id, trip_id, trip_passenger_id, event_type, created_by_role, created_by_id, occurred_at, metadata) VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa7', '11111111-1111-1111-1111-111111111111', '77777777-7777-7777-7777-777777777771', NULL, 'issue_reported', 'driver', '33333333-3333-3333-3333-333333333331', '2026-05-09 06:14:40.460077+00', '{"badge": "New", "title": "Road Accident", "message": "man", "admin_alert": "true"}');


--
-- Data for Name: notification_outbox; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.notification_outbox (id, school_id, trip_event_id, recipient_kind, recipient_phone, push_subscription_id, channel, template_key, payload, status, attempts, last_error, claimed_at, created_at, sent_at) VALUES ('99999999-9999-9999-9999-999999999991', '11111111-1111-1111-1111-111111111111', NULL, 'parent', '+254787878000', NULL, 'sms', 'child_confirmed_on_van', '{"body": "SafeRide demo SMS message."}', 'pending', 0, NULL, NULL, '2026-06-15 06:14:40.460675+00', NULL);
INSERT INTO public.notification_outbox (id, school_id, trip_event_id, recipient_kind, recipient_phone, push_subscription_id, channel, template_key, payload, status, attempts, last_error, claimed_at, created_at, sent_at) VALUES ('99999999-9999-9999-9999-999999999992', '11111111-1111-1111-1111-111111111111', NULL, 'parent', NULL, NULL, 'push', 'child_confirmed_on_van', '{"body": "SafeRide demo push message."}', 'pending', 0, NULL, NULL, '2026-06-15 06:14:40.460675+00', NULL);


--
-- Data for Name: parent_contacts; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.parent_contacts (id, school_id, student_id, contact_1_name, contact_1_phone, contact_1_relationship, contact_2_name, contact_2_phone, contact_2_relationship, created_at) VALUES ('55555555-5555-5555-5555-555555555551', '11111111-1111-1111-1111-111111111111', '44444444-4444-4444-4444-444444444441', 'Grace Achieng', '+254787878000', 'Mother', NULL, NULL, NULL, '2026-06-15 06:14:40.457771+00');
INSERT INTO public.parent_contacts (id, school_id, student_id, contact_1_name, contact_1_phone, contact_1_relationship, contact_2_name, contact_2_phone, contact_2_relationship, created_at) VALUES ('55555555-5555-5555-5555-555555555552', '11111111-1111-1111-1111-111111111111', '44444444-4444-4444-4444-444444444442', 'Jimmy Kenesa', '+254787878001', 'Father', NULL, NULL, NULL, '2026-06-15 06:14:40.457771+00');
INSERT INTO public.parent_contacts (id, school_id, student_id, contact_1_name, contact_1_phone, contact_1_relationship, contact_2_name, contact_2_phone, contact_2_relationship, created_at) VALUES ('55555555-5555-5555-5555-555555555553', '11111111-1111-1111-1111-111111111111', '44444444-4444-4444-4444-444444444443', 'George Mwangi', '+254787878002', 'Father', NULL, NULL, NULL, '2026-06-15 06:14:40.457771+00');
INSERT INTO public.parent_contacts (id, school_id, student_id, contact_1_name, contact_1_phone, contact_1_relationship, contact_2_name, contact_2_phone, contact_2_relationship, created_at) VALUES ('55555555-5555-5555-5555-555555555554', '11111111-1111-1111-1111-111111111111', '44444444-4444-4444-4444-444444444444', 'John Otieno', '+254787878003', 'Father', NULL, NULL, NULL, '2026-06-15 06:14:40.457771+00');
INSERT INTO public.parent_contacts (id, school_id, student_id, contact_1_name, contact_1_phone, contact_1_relationship, contact_2_name, contact_2_phone, contact_2_relationship, created_at) VALUES ('55555555-5555-5555-5555-555555555555', '11111111-1111-1111-1111-111111111111', '44444444-4444-4444-4444-444444444445', 'Jessica Otieno', '+254787878004', 'Mother', NULL, NULL, NULL, '2026-06-15 06:14:40.457771+00');


--
-- Data for Name: password_reset_tokens; Type: TABLE DATA; Schema: public; Owner: -
--



--
-- Data for Name: run_stops; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('45622da0-c8db-4e82-8d9f-5439bce85563', 'c07b1972-b1c0-48e2-a47b-f268340863f0', 1, 'Kilimani, Nairobi', '06:40', -1.2902, 36.7823, false, '50000000-0000-0000-0000-000000000001');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('d20791e6-2a47-4cf6-aa3e-565f5dfc16a4', 'c07b1972-b1c0-48e2-a47b-f268340863f0', 2, 'Lavington, Nairobi', '06:48', -1.2789, 36.7685, false, '50000000-0000-0000-0000-000000000003');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('cc66e287-bd03-4487-9ece-f7877ae605da', 'c07b1972-b1c0-48e2-a47b-f268340863f0', 3, 'School Pickup', '06:55', -1.333667, 36.73547, false, '50000000-0000-0000-0000-000000000004');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('e5543411-8d3b-412b-a283-a9abfd14e4e0', 'c07b1972-b1c0-48e2-a47b-f268340863f0', 4, 'Greenfield Academy', NULL, -1.333667, 36.73547, true, NULL);
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('d6fa7794-8f8d-43de-855e-f2ba37975ef5', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', 1, 'Kilimani, Nairobi', '06:40', -1.2902, 36.7823, false, '50000000-0000-0000-0000-000000000001');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('64a280ef-55de-4d75-b288-57ff8ff2bbde', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', 2, 'Lavington, Nairobi', '06:48', -1.2789, 36.7685, false, '50000000-0000-0000-0000-000000000003');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('f6edc464-9626-42a4-a822-535138cdcfc0', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', 3, 'Karen, Nairobi', '06:55', -1.3283746, 36.7049676, false, '50000000-0000-0000-0000-000000000004');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('e10950b7-87af-444a-b6e1-9a19d3a71757', 'c7252b9c-4cb2-47dd-8dfe-4d4f5619f097', 4, 'Greenfield Academy', NULL, -1.333667, 36.73547, true, NULL);
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('5133af44-a218-4e66-86b3-3a7928ada9d7', '54bd5a88-a1f4-4483-9e55-25140db7d219', 1, 'Kilimani, Nairobi', '06:40', -1.2902, 36.7823, false, '50000000-0000-0000-0000-000000000001');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('4423d7c8-398a-4711-8733-6c7e606903bd', '54bd5a88-a1f4-4483-9e55-25140db7d219', 2, 'Lavington, Nairobi', '06:48', -1.2789, 36.7685, false, '50000000-0000-0000-0000-000000000003');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('682916b8-aeae-42dc-a8e2-145ff2814019', '54bd5a88-a1f4-4483-9e55-25140db7d219', 3, 'Karen, Nairobi', '06:55', -1.3283746, 36.7049676, false, '50000000-0000-0000-0000-000000000004');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('d36d39aa-6ee5-4afc-9d62-e8cbb4a74411', '54bd5a88-a1f4-4483-9e55-25140db7d219', 4, 'Greenfield Academy', NULL, -1.333667, 36.73547, true, NULL);
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('13e330ed-4ad3-4dcb-9cb9-b7678dba8379', '6b6cba81-a13f-42ca-aefb-f2c8a4a2e994', 1, 'Kilimani, Nairobi', '06:40', -1.2902, 36.7823, false, '50000000-0000-0000-0000-000000000001');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('5bcfa72f-6482-4471-9885-23c348d6d597', '6b6cba81-a13f-42ca-aefb-f2c8a4a2e994', 2, 'Lavington, Nairobi', '06:48', -1.2789, 36.7685, false, '50000000-0000-0000-0000-000000000003');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('23d49e7b-84d7-4acd-8510-cfd03bb81adc', '6b6cba81-a13f-42ca-aefb-f2c8a4a2e994', 3, 'Karen, Nairobi', '06:55', -1.3283746, 36.7049676, false, '50000000-0000-0000-0000-000000000004');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('7405d61a-6c56-45bc-8377-2d7b259008b0', '6b6cba81-a13f-42ca-aefb-f2c8a4a2e994', 4, 'Greenfield Academy', NULL, -1.333667, 36.73547, true, NULL);
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('15dea006-2a95-4214-bc95-60f6a1643dba', '2e796f4f-4ed2-4b3a-8066-7f9985c950a7', 1, 'Kilimani, Nairobi', '06:40', -1.2902, 36.7823, false, '50000000-0000-0000-0000-000000000001');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('55fa9e05-255e-4784-ba7a-6d66921a3f7a', '2e796f4f-4ed2-4b3a-8066-7f9985c950a7', 2, 'Lavington, Nairobi', '06:48', -1.2789, 36.7685, false, '50000000-0000-0000-0000-000000000003');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('4e9ef72b-8044-4e27-b9d1-f49b9bac4708', '2e796f4f-4ed2-4b3a-8066-7f9985c950a7', 3, 'Karen, Nairobi', '06:55', -1.3283746, 36.7049676, false, '50000000-0000-0000-0000-000000000004');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('b502d10d-b6bd-41a0-997e-0e6dde9be48e', '2e796f4f-4ed2-4b3a-8066-7f9985c950a7', 4, 'Greenfield Academy', NULL, -1.333667, 36.73547, true, NULL);
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('f6054709-667b-44a6-8b7e-f762454503ea', '967d7061-d005-4af2-b255-3a31f565fc8c', 1, 'Kilimani, Nairobi', '06:40', -1.2902, 36.7823, false, '50000000-0000-0000-0000-000000000001');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('77425ffb-5ab0-4ea9-9487-e9426891872e', '967d7061-d005-4af2-b255-3a31f565fc8c', 2, 'Lavington, Nairobi', '06:48', -1.2789, 36.7685, false, '50000000-0000-0000-0000-000000000003');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('29d93718-3b9f-41cc-adfd-1b43812d983c', '967d7061-d005-4af2-b255-3a31f565fc8c', 3, 'Karen, Nairobi', '06:55', -1.3283746, 36.7049676, false, '50000000-0000-0000-0000-000000000004');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('b39b0938-12b3-4036-99c5-e413881e77b0', '967d7061-d005-4af2-b255-3a31f565fc8c', 4, 'Greenfield Academy', NULL, -1.333667, 36.73547, true, NULL);
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('cf5c8b9b-2632-4c58-8c59-e0891077bf4d', 'c78b07bb-d18e-4caa-a21c-976bdc489743', 1, 'Kilimani, Nairobi', '06:40', -1.2902, 36.7823, false, '50000000-0000-0000-0000-000000000001');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('b355c4c5-b80b-403d-b0e7-1a0ffd760096', 'c78b07bb-d18e-4caa-a21c-976bdc489743', 2, 'Lavington, Nairobi', '06:48', -1.2789, 36.7685, false, '50000000-0000-0000-0000-000000000003');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('4f71bdf1-85fe-465d-a1d6-ba41dd379ee8', 'c78b07bb-d18e-4caa-a21c-976bdc489743', 3, 'Karen, Nairobi', '06:55', -1.3283746, 36.7049676, false, '50000000-0000-0000-0000-000000000004');
INSERT INTO public.run_stops (id, run_id, stop_order, name, scheduled_time, lat, lng, is_school_gate, student_id) VALUES ('80a3bcdb-0018-47d0-9c93-c8b1dea3cd56', 'c78b07bb-d18e-4caa-a21c-976bdc489743', 4, 'Greenfield Academy', NULL, -1.333667, 36.73547, true, NULL);


--
-- PostgreSQL database dump complete
--



SET session_replication_role = DEFAULT;

-- Local test-fixture extension (NOT part of the prod snapshot) ---------------
-- The snapshot has no bus-less child linked to the demo parent, but the test
-- suites assert that entity class (a child card without driver actions on the
-- parent home). Guarded like seeds 001/002: demo data must never load unless
-- the session explicitly opts in (scripts/reset-local-db.sh does this).
DO $$
BEGIN
  IF coalesce(current_setting('saferide.allow_demo_seed', true), '') <> 'yes' THEN
    RAISE EXCEPTION 'Demo seed blocked: local development only. Set saferide.allow_demo_seed = ''yes'' in this session to apply it.';
  END IF;
END $$;

-- Grace Njeri: Amina Achieng's bus-less child (bus_id is null on purpose).
INSERT INTO public.live_students (id, name, grade, parent_name, parent_phone, parent_phone2, parent_email, home_address, home_lat, home_lng, pickup_time, status, bus_id, school_id, boarding_stop_name, created_at)
VALUES ('50000000-0000-0000-0000-000000000005', 'Grace Njeri', 'Grade 1', 'Amina Achieng', '+254700000002', NULL, 'and7005@gmail.com', 'Karen, Nairobi', -1.319400, 36.706800, '07:00', 'at-school', NULL, '5cae0000-0000-0000-0000-000000000001', NULL, '2026-06-15 06:14:40.564728+00')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.live_parent_students (id, parent_id, student_id)
VALUES ('52000000-0000-0000-0000-000000000005', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000005')
ON CONFLICT (id) DO NOTHING;

-- Post-007 normalization: fresh bootstraps apply migrations (incl. 007's
-- backfills) to an EMPTY database and only then load this pre-007-shaped
-- snapshot, so the one-shot backfills must be replayed here idempotently to
-- keep fresh environments identical to migrated production.
UPDATE public.live_notifications n
SET run_type = r.type
FROM public.live_runs r
WHERE n.run_id = r.id AND n.run_type IS NULL;

UPDATE public.live_incidents i
SET run_type = r.type
FROM public.live_runs r
WHERE i.run_id = r.id AND i.run_type IS NULL;
