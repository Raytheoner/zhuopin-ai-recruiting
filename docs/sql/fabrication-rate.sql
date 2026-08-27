-- 编造率口径 SQL —— 定义见 docs/m1-fabrication-rate.md，不要脱离那份文档单独使用本文件。
-- 用法：sqlite3 data/demo.db < docs/sql/fabrication-rate.sql
SELECT COALESCE(llm_response_model, '(未记录)')        AS model,
       COUNT(*)                                        AS turns,
       SUM(json_array_length(written_fields))          AS written_fields,
       SUM(json_array_length(ungrounded_fields))       AS ungrounded_fields,
       ROUND(1.0 * SUM(json_array_length(ungrounded_fields))
             / NULLIF(SUM(json_array_length(written_fields)), 0), 4) AS fabrication_rate_lower_bound
FROM job_profile
GROUP BY COALESCE(llm_response_model, '(未记录)')
ORDER BY model;
