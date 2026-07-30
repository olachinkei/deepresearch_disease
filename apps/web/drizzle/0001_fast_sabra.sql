CREATE TABLE `feedback_revisions` (
	`id` text PRIMARY KEY NOT NULL,
	`feedback_id` text NOT NULL,
	`turn_id` text NOT NULL,
	`user_id` text NOT NULL,
	`vote` text NOT NULL,
	`reason` text,
	`comment` text,
	`revision` integer NOT NULL,
	`sync_status` text NOT NULL,
	`attempts` integer NOT NULL,
	`next_attempt_at` text,
	`last_error` text,
	`weave_feedback_id` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	`archived_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`turn_id`) REFERENCES `turns`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`user_id`) REFERENCES `local_users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `feedback_revisions_feedback_revision_unique` ON `feedback_revisions` (`feedback_id`,`revision`);--> statement-breakpoint
CREATE INDEX `feedback_revisions_turn_user_idx` ON `feedback_revisions` (`turn_id`,`user_id`);--> statement-breakpoint
DROP INDEX `feedback_turn_idx`;--> statement-breakpoint
ALTER TABLE `feedback_queue` ADD `revision` integer DEFAULT 1 NOT NULL;--> statement-breakpoint
INSERT OR IGNORE INTO `feedback_revisions` (
	`id`, `feedback_id`, `turn_id`, `user_id`, `vote`, `reason`, `comment`,
	`revision`, `sync_status`, `attempts`, `next_attempt_at`, `last_error`,
	`weave_feedback_id`, `created_at`, `updated_at`
)
SELECT
	`current`.`id` || ':migration',
	`current`.`id`,
	`current`.`turn_id`,
	`current`.`user_id`,
	`current`.`vote`,
	`current`.`reason`,
	`current`.`comment`,
	1,
	`current`.`sync_status`,
	`current`.`attempts`,
	`current`.`next_attempt_at`,
	`current`.`last_error`,
	`current`.`weave_feedback_id`,
	`current`.`created_at`,
	`current`.`updated_at`
FROM `feedback_queue` AS `current`
WHERE EXISTS (
	SELECT 1
	FROM `feedback_queue` AS `newer`
	WHERE `newer`.`turn_id` = `current`.`turn_id`
	  AND `newer`.`user_id` = `current`.`user_id`
	  AND (
		`newer`.`updated_at` > `current`.`updated_at`
		OR (
			`newer`.`updated_at` = `current`.`updated_at`
			AND `newer`.`created_at` > `current`.`created_at`
		)
		OR (
			`newer`.`updated_at` = `current`.`updated_at`
			AND `newer`.`created_at` = `current`.`created_at`
			AND `newer`.`id` > `current`.`id`
		)
	  )
);--> statement-breakpoint
DELETE FROM `feedback_queue` AS `current`
WHERE EXISTS (
	SELECT 1
	FROM `feedback_queue` AS `newer`
	WHERE `newer`.`turn_id` = `current`.`turn_id`
	  AND `newer`.`user_id` = `current`.`user_id`
	  AND (
		`newer`.`updated_at` > `current`.`updated_at`
		OR (
			`newer`.`updated_at` = `current`.`updated_at`
			AND `newer`.`created_at` > `current`.`created_at`
		)
		OR (
			`newer`.`updated_at` = `current`.`updated_at`
			AND `newer`.`created_at` = `current`.`created_at`
			AND `newer`.`id` > `current`.`id`
		)
	  )
);--> statement-breakpoint
CREATE UNIQUE INDEX `feedback_turn_user_unique` ON `feedback_queue` (`turn_id`,`user_id`);
