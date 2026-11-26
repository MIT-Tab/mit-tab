-- MIT-Tab MySQL baseline at tab.0059_bulkcheckinchange and
-- registration.0008_registration_schedule_eastern_time.
-- Use the target database's default utf8mb4 collation.

CREATE TABLE IF NOT EXISTS `auth_group` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(150) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `auth_group_permissions` (
  `id` int NOT NULL AUTO_INCREMENT,
  `group_id` int NOT NULL,
  `permission_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `auth_group_permissions_group_id_permission_id_0cd325b0_uniq` (`group_id`,`permission_id`),
  KEY `auth_group_permissio_permission_id_84c5c92e_fk_auth_perm` (`permission_id`),
  CONSTRAINT `auth_group_permissio_permission_id_84c5c92e_fk_auth_perm` FOREIGN KEY (`permission_id`) REFERENCES `auth_permission` (`id`),
  CONSTRAINT `auth_group_permissions_group_id_b120cbf9_fk_auth_group_id` FOREIGN KEY (`group_id`) REFERENCES `auth_group` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `auth_permission` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `content_type_id` int NOT NULL,
  `codename` varchar(100) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `auth_permission_content_type_id_codename_01ab375a_uniq` (`content_type_id`,`codename`),
  CONSTRAINT `auth_permission_content_type_id_2f476e4b_fk_django_co` FOREIGN KEY (`content_type_id`) REFERENCES `django_content_type` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `auth_user` (
  `id` int NOT NULL AUTO_INCREMENT,
  `password` varchar(128) NOT NULL,
  `last_login` datetime(6) DEFAULT NULL,
  `is_superuser` tinyint(1) NOT NULL,
  `username` varchar(150) NOT NULL,
  `first_name` varchar(150) NOT NULL,
  `last_name` varchar(150) NOT NULL,
  `email` varchar(254) NOT NULL,
  `is_staff` tinyint(1) NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  `date_joined` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `auth_user_groups` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `group_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `auth_user_groups_user_id_group_id_94350c0c_uniq` (`user_id`,`group_id`),
  KEY `auth_user_groups_group_id_97559544_fk_auth_group_id` (`group_id`),
  CONSTRAINT `auth_user_groups_group_id_97559544_fk_auth_group_id` FOREIGN KEY (`group_id`) REFERENCES `auth_group` (`id`),
  CONSTRAINT `auth_user_groups_user_id_6a12ed8b_fk_auth_user_id` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `auth_user_user_permissions` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `permission_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `auth_user_user_permissions_user_id_permission_id_14a6b632_uniq` (`user_id`,`permission_id`),
  KEY `auth_user_user_permi_permission_id_1fbb5f2c_fk_auth_perm` (`permission_id`),
  CONSTRAINT `auth_user_user_permi_permission_id_1fbb5f2c_fk_auth_perm` FOREIGN KEY (`permission_id`) REFERENCES `auth_permission` (`id`),
  CONSTRAINT `auth_user_user_permissions_user_id_a95ead1b_fk_auth_user_id` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `django_admin_log` (
  `id` int NOT NULL AUTO_INCREMENT,
  `action_time` datetime(6) NOT NULL,
  `object_id` longtext,
  `object_repr` varchar(200) NOT NULL,
  `action_flag` smallint unsigned NOT NULL,
  `change_message` longtext NOT NULL,
  `content_type_id` int DEFAULT NULL,
  `user_id` int NOT NULL,
  PRIMARY KEY (`id`),
  KEY `django_admin_log_content_type_id_c4bce8eb_fk_django_co` (`content_type_id`),
  KEY `django_admin_log_user_id_c564eba6_fk_auth_user_id` (`user_id`),
  CONSTRAINT `django_admin_log_content_type_id_c4bce8eb_fk_django_co` FOREIGN KEY (`content_type_id`) REFERENCES `django_content_type` (`id`),
  CONSTRAINT `django_admin_log_user_id_c564eba6_fk_auth_user_id` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`),
  CONSTRAINT `django_admin_log_chk_1` CHECK ((`action_flag` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `django_content_type` (
  `id` int NOT NULL AUTO_INCREMENT,
  `app_label` varchar(100) NOT NULL,
  `model` varchar(100) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `django_content_type_app_label_model_76bd3d3b_uniq` (`app_label`,`model`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `django_migrations` (
  `id` int NOT NULL AUTO_INCREMENT,
  `app` varchar(255) NOT NULL,
  `name` varchar(255) NOT NULL,
  `applied` datetime(6) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `django_session` (
  `session_key` varchar(40) NOT NULL,
  `session_data` longtext NOT NULL,
  `expire_date` datetime(6) NOT NULL,
  PRIMARY KEY (`session_key`),
  KEY `django_session_expire_date_a5c62663` (`expire_date`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `registration_infolink` (
  `id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(80) NOT NULL,
  `url` varchar(500) NOT NULL,
  `description` varchar(160) NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  `display_order` int unsigned NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `registration_infolink_chk_1` CHECK ((`display_order` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `registration_infotext` (
  `id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(80) NOT NULL,
  `text` varchar(500) NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  `display_order` int unsigned NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `registration_infotext_chk_1` CHECK ((`display_order` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `registration_registration` (
  `id` int NOT NULL AUTO_INCREMENT,
  `email` varchar(254) NOT NULL,
  `herokunator_code` varchar(255) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `school_id` int NOT NULL,
  `dietary_requests` longtext NOT NULL DEFAULT (_utf8mb4''),
  `housing_requests` longtext NOT NULL DEFAULT (_utf8mb4''),
  PRIMARY KEY (`id`),
  UNIQUE KEY `herokunator_code` (`herokunator_code`),
  KEY `registration_registration_school_id_2fb48efe_fk_tab_school_id` (`school_id`),
  CONSTRAINT `registration_registration_school_id_2fb48efe_fk_tab_school_id` FOREIGN KEY (`school_id`) REFERENCES `tab_school` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `registration_registrationchangelog` (
  `id` int NOT NULL AUTO_INCREMENT,
  `registration_code` varchar(255) NOT NULL,
  `school_name` varchar(50) NOT NULL,
  `email` varchar(254) NOT NULL,
  `action` varchar(20) NOT NULL,
  `summary` longtext NOT NULL,
  `changes` json NOT NULL,
  `snapshot` json NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `registration_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `registration_registr_registration_id_1ba314f3_fk_registrat` (`registration_id`),
  CONSTRAINT `registration_registr_registration_id_1ba314f3_fk_registrat` FOREIGN KEY (`registration_id`) REFERENCES `registration_registration` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `registration_registrationconfig` (
  `id` int NOT NULL AUTO_INCREMENT,
  `allow_new_registrations` tinyint(1) NOT NULL,
  `allow_registration_edits` tinyint(1) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `disc_scratch_quantity` smallint unsigned NOT NULL,
  `disc_scratches_open` tinyint(1) NOT NULL,
  `team_name_changes_allowed` tinyint(1) NOT NULL,
  `crash_housing_enabled` tinyint(1) NOT NULL,
  `new_registrations_close_at` datetime(6) DEFAULT NULL,
  `new_registrations_open_at` datetime(6) DEFAULT NULL,
  `registration_edits_close_at` datetime(6) DEFAULT NULL,
  `registration_edits_open_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `registration_registrationconfig_chk_1` CHECK ((`disc_scratch_quantity` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `registration_registrationconfirmationemaillog` (
  `id` int NOT NULL AUTO_INCREMENT,
  `email` varchar(254) NOT NULL,
  `successful` tinyint(1) NOT NULL,
  `error_message` longtext NOT NULL,
  `sent_at` datetime(6) NOT NULL,
  `registration_id` int NOT NULL,
  `email_payload` json NOT NULL DEFAULT (_utf8mb4'{}'),
  PRIMARY KEY (`id`),
  KEY `registratio_registr_c8c8fd_idx` (`registration_id`,`sent_at`),
  KEY `registratio_email_f224f3_idx` (`email`,`sent_at`),
  CONSTRAINT `registration_registr_registration_id_1fb21c2c_fk_registrat` FOREIGN KEY (`registration_id`) REFERENCES `registration_registration` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `registration_registrationlink` (
  `id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(80) NOT NULL,
  `url` varchar(500) NOT NULL,
  `description` varchar(160) NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  `display_order` int unsigned NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `registration_registrationlink_chk_1` CHECK ((`display_order` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_auditevent` (
  `id` int NOT NULL AUTO_INCREMENT,
  `object_id` int unsigned NOT NULL,
  `event_type` varchar(40) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `object_repr` varchar(255) NOT NULL,
  `changes` json NOT NULL,
  `note` longtext NOT NULL,
  `content_type_id` int NOT NULL,
  `user_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `tab_auditev_content_6748f6_idx` (`content_type_id`,`object_id`,`created_at`),
  KEY `tab_auditev_event_t_08bae1_idx` (`event_type`,`created_at`),
  KEY `tab_auditevent_user_id_512e92a4_fk_auth_user_id` (`user_id`),
  CONSTRAINT `tab_auditevent_content_type_id_659d9027_fk_django_co` FOREIGN KEY (`content_type_id`) REFERENCES `django_content_type` (`id`),
  CONSTRAINT `tab_auditevent_user_id_512e92a4_fk_auth_user_id` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`),
  CONSTRAINT `tab_auditevent_chk_1` CHECK ((`object_id` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_breakingteam` (
  `id` int NOT NULL AUTO_INCREMENT,
  `seed` int NOT NULL,
  `effective_seed` int NOT NULL,
  `type_of_team` int NOT NULL,
  `team_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `team_id` (`team_id`),
  CONSTRAINT `tab_breakingteam_team_id_9c78e270_fk_tab_team_id` FOREIGN KEY (`team_id`) REFERENCES `tab_team` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_bulkcheckinchange` (
  `id` int NOT NULL AUTO_INCREMENT,
  `token` char(32) NOT NULL,
  `entity_type` varchar(14) NOT NULL,
  `checked` tinyint(1) NOT NULL,
  `cells` json NOT NULL,
  `undone` tinyint(1) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `token` (`token`),
  KEY `tab_bulkche_entity__08aaa7_idx` (`entity_type`,`undone`,`id` DESC)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_bye` (
  `id` int NOT NULL AUTO_INCREMENT,
  `round_number` int NOT NULL,
  `bye_team_id` int NOT NULL,
  PRIMARY KEY (`id`),
  KEY `tab_bye_bye_team_id_626bc31f_fk_tab_team_id` (`bye_team_id`),
  CONSTRAINT `tab_bye_bye_team_id_626bc31f_fk_tab_team_id` FOREIGN KEY (`bye_team_id`) REFERENCES `tab_team` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_checkin` (
  `id` int NOT NULL AUTO_INCREMENT,
  `round_number` int NOT NULL,
  `judge_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_judge_checkin_per_round` (`judge_id`,`round_number`),
  CONSTRAINT `tab_checkin_judge_id_30861f5c_fk_tab_judge_id` FOREIGN KEY (`judge_id`) REFERENCES `tab_judge` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_debater` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(30) NOT NULL,
  `novice_status` int NOT NULL,
  `tiebreaker` int DEFAULT NULL,
  `apda_id` int DEFAULT NULL,
  `email` varchar(254) DEFAULT NULL,
  `qualified` tinyint(1) NOT NULL,
  `school_id` int DEFAULT NULL,
  `needs_crash_housing` tinyint(1) NOT NULL,
  `requests_wgm_housing` tinyint(1) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`),
  UNIQUE KEY `tiebreaker` (`tiebreaker`),
  KEY `tab_debater_school_id_a6332a6f_fk_tab_school_id` (`school_id`),
  CONSTRAINT `tab_debater_school_id_a6332a6f_fk_tab_school_id` FOREIGN KEY (`school_id`) REFERENCES `tab_school` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_judge` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(30) NOT NULL,
  `rank` decimal(4,2) NOT NULL,
  `ballot_code` varchar(255) DEFAULT NULL,
  `wing_only` tinyint(1) NOT NULL,
  `is_dino` tinyint(1) NOT NULL,
  `email` varchar(254) DEFAULT NULL,
  `created_at` datetime(6) DEFAULT NULL,
  `created_by_id` int DEFAULT NULL,
  `registration_id` int DEFAULT NULL,
  `request_wing` tinyint(1) NOT NULL,
  `no_wings` tinyint(1) NOT NULL,
  `needs_crash_housing` tinyint(1) NOT NULL,
  `reported_experience` smallint unsigned DEFAULT NULL,
  `requests_wgm_housing` tinyint(1) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`),
  UNIQUE KEY `ballot_code` (`ballot_code`),
  KEY `tab_judge_created_by_id_ae1b4fa2_fk_auth_user_id` (`created_by_id`),
  KEY `tab_judge_registration_id_00c393c5_fk_registrat` (`registration_id`),
  CONSTRAINT `tab_judge_created_by_id_ae1b4fa2_fk_auth_user_id` FOREIGN KEY (`created_by_id`) REFERENCES `auth_user` (`id`),
  CONSTRAINT `tab_judge_registration_id_00c393c5_fk_registrat` FOREIGN KEY (`registration_id`) REFERENCES `registration_registration` (`id`),
  CONSTRAINT `tab_judge_chk_1` CHECK ((`reported_experience` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_judge_required_room_tags` (
  `id` int NOT NULL AUTO_INCREMENT,
  `judge_id` int NOT NULL,
  `roomtag_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tab_judge_required_room_tags_judge_id_roomtag_id_8a9cd9c9_uniq` (`judge_id`,`roomtag_id`),
  KEY `tab_judge_required_r_roomtag_id_b754be74_fk_tab_roomt` (`roomtag_id`),
  CONSTRAINT `tab_judge_required_r_roomtag_id_b754be74_fk_tab_roomt` FOREIGN KEY (`roomtag_id`) REFERENCES `tab_roomtag` (`id`),
  CONSTRAINT `tab_judge_required_room_tags_judge_id_2558ba29_fk_tab_judge_id` FOREIGN KEY (`judge_id`) REFERENCES `tab_judge` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_judge_schools` (
  `id` int NOT NULL AUTO_INCREMENT,
  `judge_id` int NOT NULL,
  `school_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tab_judge_schools_judge_id_school_id_605fcdb4_uniq` (`judge_id`,`school_id`),
  KEY `tab_judge_schools_school_id_88ae0ecd_fk_tab_school_id` (`school_id`),
  CONSTRAINT `tab_judge_schools_judge_id_9b80bc98_fk_tab_judge_id` FOREIGN KEY (`judge_id`) REFERENCES `tab_judge` (`id`),
  CONSTRAINT `tab_judge_schools_school_id_88ae0ecd_fk_tab_school_id` FOREIGN KEY (`school_id`) REFERENCES `tab_school` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_judgecodeemaillog` (
  `id` int NOT NULL AUTO_INCREMENT,
  `email` varchar(254) NOT NULL,
  `ballot_code` varchar(30) NOT NULL,
  `sent_at` datetime(6) NOT NULL,
  `judge_id` int NOT NULL,
  `source` varchar(20) NOT NULL,
  `email_payload` json NOT NULL DEFAULT (_utf8mb4'{}'),
  PRIMARY KEY (`id`),
  KEY `tab_judgeco_email_4b0e7c_idx` (`email`,`sent_at`),
  KEY `tab_judgeco_judge_i_98bb44_idx` (`judge_id`,`sent_at`),
  CONSTRAINT `tab_judgecodeemaillog_judge_id_5f374e59_fk_tab_judge_id` FOREIGN KEY (`judge_id`) REFERENCES `tab_judge` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_judgeexpectedcheckin` (
  `id` int NOT NULL AUTO_INCREMENT,
  `round_number` int NOT NULL,
  `judge_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_judge_expectation_per_round` (`judge_id`,`round_number`),
  CONSTRAINT `tab_judgeexpectedcheckin_judge_id_16d0a9dc_fk_tab_judge_id` FOREIGN KEY (`judge_id`) REFERENCES `tab_judge` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_manualjudgeassignment` (
  `id` int NOT NULL AUTO_INCREMENT,
  `created_at` datetime(6) NOT NULL,
  `judge_id` int NOT NULL,
  `round_id` int NOT NULL,
  `created_by_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tab_manualjudgeassignment_round_id_judge_id_6149e5b3_uniq` (`round_id`,`judge_id`),
  KEY `tab_manualjudgeassignment_judge_id_5e9d9db0_fk_tab_judge_id` (`judge_id`),
  KEY `tab_manualjudgeassignment_created_by_id_6dcacd5c_fk_auth_user_id` (`created_by_id`),
  CONSTRAINT `tab_manualjudgeassignment_created_by_id_6dcacd5c_fk_auth_user_id` FOREIGN KEY (`created_by_id`) REFERENCES `auth_user` (`id`),
  CONSTRAINT `tab_manualjudgeassignment_judge_id_5e9d9db0_fk_tab_judge_id` FOREIGN KEY (`judge_id`) REFERENCES `tab_judge` (`id`),
  CONSTRAINT `tab_manualjudgeassignment_round_id_162e272c_fk_tab_round_id` FOREIGN KEY (`round_id`) REFERENCES `tab_round` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_motion` (
  `id` int NOT NULL AUTO_INCREMENT,
  `round_number` int DEFAULT NULL,
  `outround_type` int DEFAULT NULL,
  `num_teams` int DEFAULT NULL,
  `info_slide` longtext NOT NULL,
  `motion_text` longtext NOT NULL,
  `is_published` tinyint(1) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_noshow` (
  `id` int NOT NULL AUTO_INCREMENT,
  `round_number` int NOT NULL,
  `no_show_team_id` int NOT NULL,
  PRIMARY KEY (`id`),
  KEY `tab_noshow_no_show_team_id_0b285c08_fk_tab_team_id` (`no_show_team_id`),
  CONSTRAINT `tab_noshow_no_show_team_id_0b285c08_fk_tab_team_id` FOREIGN KEY (`no_show_team_id`) REFERENCES `tab_team` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_outround` (
  `id` int NOT NULL AUTO_INCREMENT,
  `num_teams` int NOT NULL,
  `type_of_round` int NOT NULL,
  `victor` int NOT NULL,
  `chair_id` int DEFAULT NULL,
  `gov_team_id` int NOT NULL,
  `opp_team_id` int NOT NULL,
  `room_id` int DEFAULT NULL,
  `sidelock` tinyint(1) NOT NULL,
  `choice` int NOT NULL,
  PRIMARY KEY (`id`),
  KEY `tab_outround_chair_id_5796ba70_fk_tab_judge_id` (`chair_id`),
  KEY `tab_outround_gov_team_id_8550e86b_fk_tab_team_id` (`gov_team_id`),
  KEY `tab_outround_opp_team_id_de5dd45c_fk_tab_team_id` (`opp_team_id`),
  KEY `tab_outround_room_id_59ab92e6_fk_tab_room_id` (`room_id`),
  CONSTRAINT `tab_outround_chair_id_5796ba70_fk_tab_judge_id` FOREIGN KEY (`chair_id`) REFERENCES `tab_judge` (`id`),
  CONSTRAINT `tab_outround_gov_team_id_8550e86b_fk_tab_team_id` FOREIGN KEY (`gov_team_id`) REFERENCES `tab_team` (`id`),
  CONSTRAINT `tab_outround_opp_team_id_de5dd45c_fk_tab_team_id` FOREIGN KEY (`opp_team_id`) REFERENCES `tab_team` (`id`),
  CONSTRAINT `tab_outround_room_id_59ab92e6_fk_tab_room_id` FOREIGN KEY (`room_id`) REFERENCES `tab_room` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_outround_judges` (
  `id` int NOT NULL AUTO_INCREMENT,
  `outround_id` int NOT NULL,
  `judge_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tab_outround_judges_outround_id_judge_id_0ea8a2b2_uniq` (`outround_id`,`judge_id`),
  KEY `tab_outround_judges_judge_id_d4e17a59_fk_tab_judge_id` (`judge_id`),
  CONSTRAINT `tab_outround_judges_judge_id_d4e17a59_fk_tab_judge_id` FOREIGN KEY (`judge_id`) REFERENCES `tab_judge` (`id`),
  CONSTRAINT `tab_outround_judges_outround_id_5262b2a5_fk_tab_outround_id` FOREIGN KEY (`outround_id`) REFERENCES `tab_outround` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_outroundchallenge` (
  `id` int NOT NULL AUTO_INCREMENT,
  `num_teams` int NOT NULL,
  `type_of_round` int NOT NULL,
  `challenged_id` int NOT NULL,
  `challenger_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_challenger_per_outround_size` (`num_teams`,`type_of_round`,`challenger_id`),
  UNIQUE KEY `unique_target_per_outround_size` (`num_teams`,`type_of_round`,`challenged_id`),
  KEY `tab_outroundchallenge_challenged_id_7a0dc471_fk_tab_team_id` (`challenged_id`),
  KEY `tab_outroundchallenge_challenger_id_ef3455f4_fk_tab_team_id` (`challenger_id`),
  CONSTRAINT `tab_outroundchallenge_challenged_id_7a0dc471_fk_tab_team_id` FOREIGN KEY (`challenged_id`) REFERENCES `tab_team` (`id`),
  CONSTRAINT `tab_outroundchallenge_challenger_id_ef3455f4_fk_tab_team_id` FOREIGN KEY (`challenger_id`) REFERENCES `tab_team` (`id`),
  CONSTRAINT `challenge_distinct_teams` CHECK ((`challenger_id` <> `challenged_id`))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_publicdisplaysetting` (
  `id` int NOT NULL AUTO_INCREMENT,
  `slug` varchar(50) NOT NULL,
  `label` varchar(100) NOT NULL,
  `display_type` varchar(20) NOT NULL,
  `is_enabled` tinyint(1) NOT NULL,
  `include_speaks` tinyint(1) NOT NULL,
  `include_ranks` tinyint(1) NOT NULL,
  `max_visible` int unsigned NOT NULL,
  `round_number` int unsigned DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `slug` (`slug`),
  CONSTRAINT `tab_publicdisplaysetting_chk_1` CHECK ((`max_visible` >= 0)),
  CONSTRAINT `tab_publicdisplaysetting_chk_2` CHECK ((`round_number` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_publichomepage` (
  `id` int NOT NULL AUTO_INCREMENT,
  `slug` varchar(50) NOT NULL,
  `title` varchar(100) NOT NULL,
  `subtitle` varchar(255) NOT NULL,
  `url_path` varchar(200) NOT NULL,
  `sort_order` smallint unsigned NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `slug` (`slug`),
  CONSTRAINT `tab_publichomepage_chk_1` CHECK ((`sort_order` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_publichomeshortcut` (
  `id` int NOT NULL AUTO_INCREMENT,
  `position` smallint unsigned NOT NULL,
  `nav_item` varchar(50) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `position` (`position`),
  KEY `tab_publichomeshortcut_nav_item_f45ba0fd` (`nav_item`),
  CONSTRAINT `tab_publichomeshortcut_chk_1` CHECK ((`position` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_publicrosterchange` (
  `id` int NOT NULL AUTO_INCREMENT,
  `list_kind` varchar(6) NOT NULL,
  `message` longtext NOT NULL,
  `involves_debaters` tinyint(1) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_publicrosterlogstate` (
  `id` int NOT NULL AUTO_INCREMENT,
  `first_published_at` datetime(6) NOT NULL,
  `started_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_publishedoutroundstage` (
  `id` int NOT NULL AUTO_INCREMENT,
  `type_of_round` int NOT NULL,
  `num_teams` int unsigned NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_published_outround_stage` (`type_of_round`,`num_teams`),
  CONSTRAINT `tab_publishedoutroundstage_chk_1` CHECK ((`num_teams` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_publishedteamplacement` (
  `id` int NOT NULL AUTO_INCREMENT,
  `place` int unsigned NOT NULL,
  `stage_id` int NOT NULL,
  `team_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_published_stage_team` (`stage_id`,`team_id`),
  KEY `tab_publishedteamplacement_team_id_c982ac14_fk_tab_team_id` (`team_id`),
  CONSTRAINT `tab_publishedteampla_stage_id_39563215_fk_tab_publi` FOREIGN KEY (`stage_id`) REFERENCES `tab_publishedoutroundstage` (`id`),
  CONSTRAINT `tab_publishedteamplacement_team_id_c982ac14_fk_tab_team_id` FOREIGN KEY (`team_id`) REFERENCES `tab_team` (`id`),
  CONSTRAINT `tab_publishedteamplacement_chk_1` CHECK ((`place` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_rankinggroup` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_rankinggroup_debaters` (
  `id` int NOT NULL AUTO_INCREMENT,
  `rankinggroup_id` int NOT NULL,
  `debater_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tab_rankinggroup_debater_rankinggroup_id_debater__340b1cb9_uniq` (`rankinggroup_id`,`debater_id`),
  KEY `tab_rankinggroup_debaters_debater_id_980c6c30_fk_tab_debater_id` (`debater_id`),
  CONSTRAINT `tab_rankinggroup_deb_rankinggroup_id_43a04b15_fk_tab_ranki` FOREIGN KEY (`rankinggroup_id`) REFERENCES `tab_rankinggroup` (`id`),
  CONSTRAINT `tab_rankinggroup_debaters_debater_id_980c6c30_fk_tab_debater_id` FOREIGN KEY (`debater_id`) REFERENCES `tab_debater` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_rankinggroup_teams` (
  `id` int NOT NULL AUTO_INCREMENT,
  `rankinggroup_id` int NOT NULL,
  `team_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tab_rankinggroup_teams_rankinggroup_id_team_id_4d748f0d_uniq` (`rankinggroup_id`,`team_id`),
  KEY `tab_rankinggroup_teams_team_id_973f5ab8_fk_tab_team_id` (`team_id`),
  CONSTRAINT `tab_rankinggroup_tea_rankinggroup_id_e5ab2210_fk_tab_ranki` FOREIGN KEY (`rankinggroup_id`) REFERENCES `tab_rankinggroup` (`id`),
  CONSTRAINT `tab_rankinggroup_teams_team_id_973f5ab8_fk_tab_team_id` FOREIGN KEY (`team_id`) REFERENCES `tab_team` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_room` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(30) NOT NULL,
  `rank` decimal(4,2) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_room_tags` (
  `id` int NOT NULL AUTO_INCREMENT,
  `room_id` int NOT NULL,
  `roomtag_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tab_room_tags_room_id_roomtag_id_ab159f4f_uniq` (`room_id`,`roomtag_id`),
  KEY `tab_room_tags_roomtag_id_ced9126a_fk_tab_roomtag_id` (`roomtag_id`),
  CONSTRAINT `tab_room_tags_room_id_54082eff_fk_tab_room_id` FOREIGN KEY (`room_id`) REFERENCES `tab_room` (`id`),
  CONSTRAINT `tab_room_tags_roomtag_id_ced9126a_fk_tab_roomtag_id` FOREIGN KEY (`roomtag_id`) REFERENCES `tab_roomtag` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_roomcheckin` (
  `id` int NOT NULL AUTO_INCREMENT,
  `round_number` int NOT NULL,
  `room_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_room_checkin_per_round` (`room_id`,`round_number`),
  CONSTRAINT `tab_roomcheckin_room_id_04f04d9c_fk_tab_room_id` FOREIGN KEY (`room_id`) REFERENCES `tab_room` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_roomtag` (
  `id` int NOT NULL AUTO_INCREMENT,
  `tag` varchar(255) NOT NULL,
  `priority` decimal(4,2) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_round` (
  `id` int NOT NULL AUTO_INCREMENT,
  `round_number` int NOT NULL,
  `pullup` int NOT NULL,
  `victor` int NOT NULL,
  `chair_id` int DEFAULT NULL,
  `gov_team_id` int NOT NULL,
  `opp_team_id` int NOT NULL,
  `room_id` int DEFAULT NULL,
  `rfd` longtext NOT NULL DEFAULT (_utf8mb4''),
  `result_entered_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `tab_round_gov_team_id_98292b80_fk_tab_team_id` (`gov_team_id`),
  KEY `tab_round_opp_team_id_a828017e_fk_tab_team_id` (`opp_team_id`),
  KEY `tab_round_chair_id_0783b921_fk_tab_judge_id` (`chair_id`),
  KEY `tab_round_room_id_69d825c5_fk_tab_room_id` (`room_id`),
  CONSTRAINT `tab_round_chair_id_0783b921_fk_tab_judge_id` FOREIGN KEY (`chair_id`) REFERENCES `tab_judge` (`id`),
  CONSTRAINT `tab_round_gov_team_id_98292b80_fk_tab_team_id` FOREIGN KEY (`gov_team_id`) REFERENCES `tab_team` (`id`),
  CONSTRAINT `tab_round_opp_team_id_a828017e_fk_tab_team_id` FOREIGN KEY (`opp_team_id`) REFERENCES `tab_team` (`id`),
  CONSTRAINT `tab_round_room_id_69d825c5_fk_tab_room_id` FOREIGN KEY (`room_id`) REFERENCES `tab_room` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_round_judges` (
  `id` int NOT NULL AUTO_INCREMENT,
  `round_id` int NOT NULL,
  `judge_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tab_round_judges_round_id_judge_id_7d4c0954_uniq` (`round_id`,`judge_id`),
  KEY `tab_round_judges_judge_id_ece3f246_fk_tab_judge_id` (`judge_id`),
  CONSTRAINT `tab_round_judges_judge_id_ece3f246_fk_tab_judge_id` FOREIGN KEY (`judge_id`) REFERENCES `tab_judge` (`id`),
  CONSTRAINT `tab_round_judges_round_id_dc833f9b_fk_tab_round_id` FOREIGN KEY (`round_id`) REFERENCES `tab_round` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_roundstats` (
  `id` int NOT NULL AUTO_INCREMENT,
  `speaks` decimal(6,4) NOT NULL,
  `ranks` decimal(6,4) NOT NULL,
  `debater_role` varchar(4) DEFAULT NULL,
  `debater_id` int NOT NULL,
  `round_id` int NOT NULL,
  PRIMARY KEY (`id`),
  KEY `tab_roundstats_debater_id_020b171e_fk_tab_debater_id` (`debater_id`),
  KEY `tab_roundstats_round_id_5f2caf6f_fk_tab_round_id` (`round_id`),
  CONSTRAINT `tab_roundstats_debater_id_020b171e_fk_tab_debater_id` FOREIGN KEY (`debater_id`) REFERENCES `tab_debater` (`id`),
  CONSTRAINT `tab_roundstats_round_id_5f2caf6f_fk_tab_round_id` FOREIGN KEY (`round_id`) REFERENCES `tab_round` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_school` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(50) NOT NULL,
  `apda_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_scratch` (
  `id` int NOT NULL AUTO_INCREMENT,
  `scratch_type` int NOT NULL,
  `judge_id` int NOT NULL,
  `team_id` int NOT NULL,
  `created_at` datetime(6) DEFAULT NULL,
  `created_by_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tab_scratch_judge_id_team_id_e4637d3a_uniq` (`judge_id`,`team_id`),
  KEY `tab_scratch_team_id_f4cfddff_fk_tab_team_id` (`team_id`),
  KEY `tab_scratch_created_by_id_bbbfb916_fk_auth_user_id` (`created_by_id`),
  CONSTRAINT `tab_scratch_created_by_id_bbbfb916_fk_auth_user_id` FOREIGN KEY (`created_by_id`) REFERENCES `auth_user` (`id`),
  CONSTRAINT `tab_scratch_judge_id_d8f59c0c_fk_tab_judge_id` FOREIGN KEY (`judge_id`) REFERENCES `tab_judge` (`id`),
  CONSTRAINT `tab_scratch_team_id_f4cfddff_fk_tab_team_id` FOREIGN KEY (`team_id`) REFERENCES `tab_team` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_staffinviteemaillog` (
  `id` int NOT NULL AUTO_INCREMENT,
  `email_payload` json NOT NULL,
  `username` varchar(150) NOT NULL,
  `email` varchar(254) NOT NULL,
  `successful` tinyint(1) NOT NULL,
  `error_message` longtext NOT NULL,
  `sent_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_tabsettings` (
  `id` int NOT NULL AUTO_INCREMENT,
  `key` varchar(50) NOT NULL,
  `value` int DEFAULT NULL,
  `value_string` varchar(200) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_team` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(35) NOT NULL,
  `seed` int NOT NULL,
  `checked_in` tinyint(1) NOT NULL,
  `school_id` int NOT NULL,
  `hybrid_school_id` int DEFAULT NULL,
  `tiebreaker` int DEFAULT NULL,
  `team_code` varchar(255) DEFAULT NULL,
  `break_preference` int NOT NULL,
  `ranking_public` tinyint(1) NOT NULL,
  `created_at` datetime(6) DEFAULT NULL,
  `created_by_id` int DEFAULT NULL,
  `registration_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`),
  UNIQUE KEY `tiebreaker` (`tiebreaker`),
  UNIQUE KEY `team_code` (`team_code`),
  KEY `tab_team_school_id_704cc479_fk_tab_school_id` (`school_id`),
  KEY `tab_team_hybrid_school_id_516ccb2b_fk_tab_school_id` (`hybrid_school_id`),
  KEY `tab_team_created_by_id_66da6487_fk_auth_user_id` (`created_by_id`),
  KEY `tab_team_registration_id_d2822737_fk_registrat` (`registration_id`),
  CONSTRAINT `tab_team_created_by_id_66da6487_fk_auth_user_id` FOREIGN KEY (`created_by_id`) REFERENCES `auth_user` (`id`),
  CONSTRAINT `tab_team_hybrid_school_id_516ccb2b_fk_tab_school_id` FOREIGN KEY (`hybrid_school_id`) REFERENCES `tab_school` (`id`),
  CONSTRAINT `tab_team_registration_id_d2822737_fk_registrat` FOREIGN KEY (`registration_id`) REFERENCES `registration_registration` (`id`),
  CONSTRAINT `tab_team_school_id_704cc479_fk_tab_school_id` FOREIGN KEY (`school_id`) REFERENCES `tab_school` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_team_debaters` (
  `id` int NOT NULL AUTO_INCREMENT,
  `team_id` int NOT NULL,
  `debater_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tab_team_debaters_team_id_debater_id_ab659337_uniq` (`team_id`,`debater_id`),
  KEY `tab_team_debaters_debater_id_e0fc5acd_fk_tab_debater_id` (`debater_id`),
  CONSTRAINT `tab_team_debaters_debater_id_e0fc5acd_fk_tab_debater_id` FOREIGN KEY (`debater_id`) REFERENCES `tab_debater` (`id`),
  CONSTRAINT `tab_team_debaters_team_id_c4e5a860_fk_tab_team_id` FOREIGN KEY (`team_id`) REFERENCES `tab_team` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_team_required_room_tags` (
  `id` int NOT NULL AUTO_INCREMENT,
  `team_id` int NOT NULL,
  `roomtag_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tab_team_required_room_tags_team_id_roomtag_id_a81c316d_uniq` (`team_id`,`roomtag_id`),
  KEY `tab_team_required_ro_roomtag_id_4623cfb4_fk_tab_roomt` (`roomtag_id`),
  CONSTRAINT `tab_team_required_ro_roomtag_id_4623cfb4_fk_tab_roomt` FOREIGN KEY (`roomtag_id`) REFERENCES `tab_roomtag` (`id`),
  CONSTRAINT `tab_team_required_room_tags_team_id_64b00e2b_fk_tab_team_id` FOREIGN KEY (`team_id`) REFERENCES `tab_team` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_teamcodeemaillog` (
  `id` int NOT NULL AUTO_INCREMENT,
  `email_payload` json NOT NULL,
  `email` varchar(254) NOT NULL,
  `team_snapshot` json NOT NULL,
  `source` varchar(20) NOT NULL,
  `successful` tinyint(1) NOT NULL,
  `error_message` longtext NOT NULL,
  `sent_at` datetime(6) NOT NULL,
  `team_id` int NOT NULL,
  PRIMARY KEY (`id`),
  KEY `tab_teamcod_team_id_fa6c15_idx` (`team_id`,`sent_at`),
  KEY `tab_teamcod_email_53855f_idx` (`email`,`sent_at`),
  CONSTRAINT `tab_teamcodeemaillog_team_id_513ce155_fk_tab_team_id` FOREIGN KEY (`team_id`) REFERENCES `tab_team` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_tournamentstaffrole` (
  `id` int NOT NULL AUTO_INCREMENT,
  `title` varchar(100) NOT NULL,
  `names` longtext NOT NULL,
  `sort_order` smallint unsigned NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `tab_tournamentstaffrole_chk_1` CHECK ((`sort_order` >= 0))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_usertournamentsetuppreference` (
  `id` int NOT NULL AUTO_INCREMENT,
  `hide_tournament_todo` tinyint(1) NOT NULL,
  `user_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `user_id` (`user_id`),
  CONSTRAINT `tab_usertournamentse_user_id_943d9460_fk_auth_user` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS `tab_writtenrfdemaillog` (
  `id` int NOT NULL AUTO_INCREMENT,
  `email` varchar(254) NOT NULL,
  `sent_at` datetime(6) NOT NULL,
  `round_id` int NOT NULL,
  `email_payload` json NOT NULL DEFAULT (_utf8mb4'{}'),
  PRIMARY KEY (`id`),
  KEY `tab_written_round_i_85db27_idx` (`round_id`,`sent_at`),
  KEY `tab_written_email_96131e_idx` (`email`,`sent_at`),
  CONSTRAINT `tab_writtenrfdemaillog_round_id_8472d556_fk_tab_round_id` FOREIGN KEY (`round_id`) REFERENCES `tab_round` (`id`)
) ENGINE=InnoDB;
