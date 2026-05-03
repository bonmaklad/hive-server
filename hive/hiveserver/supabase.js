require("dotenv").config({ path: "/opt/hive/hiveserver/.env" });

const { createClient } = require("@supabase/supabase-js");

if (!process.env.SUPABASE_URL || !process.env.SUPABASE_SERVICE_ROLE_KEY) {
  throw new Error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY");
}

const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_ROLE_KEY,
  { auth: { persistSession: false } }
);

async function getSiteById(siteId) {
  const { data, error } = await supabase
    .from("sites")
    .select("*")
    .eq("id", siteId)
    .single();

  if (error) {
    throw new Error(`Failed to load site ${siteId}: ${error.message}`);
  }

  return data;
}

module.exports = { supabase, getSiteById };
