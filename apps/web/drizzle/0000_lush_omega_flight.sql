CREATE TABLE `conversations` (
	`id` text PRIMARY KEY NOT NULL,
	`user_id` text NOT NULL,
	`title` text NOT NULL,
	`disease` text DEFAULT 'ischemic stroke' NOT NULL,
	`target_molecule` text,
	`mechanism` text,
	`research_question` text,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`user_id`) REFERENCES `local_users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `conversations_user_updated_idx` ON `conversations` (`user_id`,`updated_at`);--> statement-breakpoint
CREATE TABLE `feedback_queue` (
	`id` text PRIMARY KEY NOT NULL,
	`turn_id` text NOT NULL,
	`user_id` text NOT NULL,
	`vote` text NOT NULL,
	`reason` text,
	`comment` text,
	`sync_status` text DEFAULT 'pending' NOT NULL,
	`attempts` integer DEFAULT 0 NOT NULL,
	`next_attempt_at` text,
	`last_error` text,
	`weave_feedback_id` text,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`turn_id`) REFERENCES `turns`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`user_id`) REFERENCES `local_users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `feedback_pending_idx` ON `feedback_queue` (`sync_status`,`next_attempt_at`);--> statement-breakpoint
CREATE INDEX `feedback_turn_idx` ON `feedback_queue` (`turn_id`);--> statement-breakpoint
CREATE TABLE `local_users` (
	`id` text PRIMARY KEY NOT NULL,
	`display_name` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
CREATE TABLE `transcript_messages` (
	`id` text PRIMARY KEY NOT NULL,
	`conversation_id` text NOT NULL,
	`turn_id` text NOT NULL,
	`role` text NOT NULL,
	`content` text NOT NULL,
	`metadata_json` text,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`conversation_id`) REFERENCES `conversations`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`turn_id`) REFERENCES `turns`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `transcript_conversation_created_idx` ON `transcript_messages` (`conversation_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `transcript_turn_idx` ON `transcript_messages` (`turn_id`);--> statement-breakpoint
CREATE TABLE `turns` (
	`id` text PRIMARY KEY NOT NULL,
	`conversation_id` text NOT NULL,
	`sequence` integer NOT NULL,
	`status` text NOT NULL,
	`query` text NOT NULL,
	`error_code` text,
	`started_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`completed_at` text,
	FOREIGN KEY (`conversation_id`) REFERENCES `conversations`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `turns_conversation_sequence_unique` ON `turns` (`conversation_id`,`sequence`);--> statement-breakpoint
CREATE INDEX `turns_conversation_idx` ON `turns` (`conversation_id`);