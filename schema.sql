-- mqtt_data 库完整 schema（mysqldump 风格，由 SHOW CREATE TABLE 生成）

CREATE TABLE `sensor_data` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `device_id` varchar(64) NOT NULL COMMENT '设备标识，取自主题最后一段',
  `topic` varchar(191) NOT NULL COMMENT '完整主题',
  `sf` decimal(10,4) DEFAULT NULL COMMENT '沉降速度 Sf',
  `vf` decimal(10,4) DEFAULT NULL COMMENT '沉降比 Vf',
  `fc` decimal(10,4) DEFAULT NULL,
  `phf` decimal(6,3) DEFAULT NULL COMMENT 'pH',
  `tf` decimal(6,2) DEFAULT NULL COMMENT '温度',
  `cf` decimal(10,4) DEFAULT NULL,
  `settling_ratio` decimal(10,4) DEFAULT NULL COMMENT '可选字段',
  `reported_at` datetime(3) NOT NULL COMMENT '设备上报时间（payload 里的 timestamp）',
  `received_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '服务端入库时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_device_time` (`device_id`,`reported_at`)
) ENGINE=InnoDB AUTO_INCREMENT=10 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='传感器上报数据';

CREATE TABLE `alarms` (
  `id` varchar(64) NOT NULL,
  `payload` json NOT NULL,
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='前端生成的告警记录，原样存取';

CREATE TABLE `thresholds` (
  `device_id` varchar(64) NOT NULL,
  `param_key` varchar(16) NOT NULL,
  `low` double NOT NULL,
  `high` double NOT NULL,
  `enabled` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`device_id`,`param_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='阈值规则，兼作二期检测器配置源';

CREATE TABLE `receivers` (
  `id` varchar(32) NOT NULL,
  `name` varchar(64) NOT NULL,
  `phone` varchar(16) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='短信接收人';
