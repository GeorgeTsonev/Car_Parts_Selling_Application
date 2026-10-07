CREATE TABLE Users (
  user_id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'consumer' CHECK (role IN ('consumer','contributor','admin')),
  bio TEXT,
  contact_info TEXT,
  avatar_url TEXT,
  reputation_score INTEGER DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE Brands (
  brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  logo_url TEXT
);

CREATE TABLE Models (
  model_id INTEGER PRIMARY KEY AUTOINCREMENT,
  brand_id INTEGER NOT NULL REFERENCES Brands(brand_id),
  name TEXT NOT NULL,
  description TEXT,
  picture_url TEXT
);

CREATE TABLE Vehicle_Generations (
  generation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  model_id INTEGER NOT NULL REFERENCES Models(model_id),
  generation_name TEXT,
  start_year INTEGER NOT NULL,
  end_year INTEGER,
  overview_image_url TEXT
);

CREATE TABLE Parts (
  part_id INTEGER PRIMARY KEY AUTOINCREMENT,
  sku TEXT UNIQUE,
  name TEXT NOT NULL,
  description TEXT,
  oem_part_number TEXT,
  category TEXT CHECK (category IN ('interior','exterior','underhood','trim_clip','lighting_bracket')),
  disassembly_instructions TEXT,
  image_url TEXT,
  author_user_id INTEGER NOT NULL REFERENCES Users(user_id),
  license TEXT DEFAULT 'CC-BY-SA-4.0' CHECK (license IN ('CC0','CC-BY-4.0','CC-BY-SA-4.0','CERN-OHL-P-2.0','CERN-OHL-S-2.0')),
  forked_from_part_id INTEGER REFERENCES Parts(part_id) ON DELETE SET NULL,
  version TEXT DEFAULT '1.0.0',
  changelog TEXT,
  is_assembly INTEGER DEFAULT 0,
  parent_part_id INTEGER REFERENCES Parts(part_id) ON DELETE SET NULL,
  is_available_physical INTEGER DEFAULT 0,
  physical_print_price REAL,
  status TEXT DEFAULT 'draft' CHECK (status IN ('draft','community_tested','verified_fit','flagged')),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  manufacturing_technique TEXT CHECK (manufacturing_technique IN ('FDM','SLS','SLA','CNC')),
  material TEXT,
  manufacturing_specs TEXT
);

CREATE TABLE Part_Compatibilities (
  compatibility_id INTEGER PRIMARY KEY AUTOINCREMENT,
  part_id INTEGER NOT NULL REFERENCES Parts(part_id) ON DELETE CASCADE,
  generation_id INTEGER NOT NULL REFERENCES Vehicle_Generations(generation_id) ON DELETE CASCADE,
  notes TEXT,
  hotspot_x REAL,
  hotspot_y REAL,
  UNIQUE (part_id, generation_id)
);

CREATE TABLE Part_Files (
  file_id INTEGER PRIMARY KEY AUTOINCREMENT,
  part_id INTEGER NOT NULL REFERENCES Parts(part_id) ON DELETE CASCADE,
  file_type TEXT NOT NULL CHECK (file_type IN ('mesh_stl','mesh_3mf','cad_step','cad_source','assembly_manual_pdf')),
  r2_object_key TEXT NOT NULL,
  file_name TEXT NOT NULL,
  file_size_bytes INTEGER,
  sha256_checksum TEXT,
  recommended_material TEXT DEFAULT 'ASA' CHECK (recommended_material IN ('PLA','PETG','ABS','ASA','PA_CF','TPU')),
  recommended_infill_pct INTEGER DEFAULT 40,
  supports_required INTEGER DEFAULT 0
);

CREATE TABLE Part_Download_Logs (
  log_id INTEGER PRIMARY KEY AUTOINCREMENT,
  part_id INTEGER NOT NULL REFERENCES Parts(part_id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES Part_Files(file_id) ON DELETE CASCADE,
  user_id INTEGER,
  downloaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
  ip_country TEXT
);

CREATE TABLE Cart_Items (
  cart_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES Users(user_id) ON DELETE CASCADE,
  part_id INTEGER NOT NULL REFERENCES Parts(part_id) ON DELETE CASCADE,
  quantity INTEGER DEFAULT 1,
  material_printed TEXT DEFAULT 'ASA' CHECK (material_printed IN ('PETG','ASA','PA_CF')),
  color TEXT DEFAULT 'Black',
  UNIQUE (user_id, part_id)
);

CREATE TABLE Physical_Orders (
  order_id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES Users(user_id),
  total_amount REAL NOT NULL,
  order_status TEXT DEFAULT 'pending' CHECK (order_status IN ('pending','paid','printing','shipped','delivered','cancelled')),
  shipping_address TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE Physical_Order_Items (
  item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES Physical_Orders(order_id) ON DELETE CASCADE,
  part_id INTEGER NOT NULL REFERENCES Parts(part_id),
  quantity INTEGER DEFAULT 1,
  unit_price REAL NOT NULL,
  material_printed TEXT NOT NULL CHECK (material_printed IN ('PETG','ASA','PA_CF')),
  color TEXT DEFAULT 'Black'
);

CREATE TABLE Part_Assemblies (
  assembly_id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_part_id INTEGER NOT NULL REFERENCES Parts(part_id) ON DELETE CASCADE,
  child_part_id INTEGER NOT NULL REFERENCES Parts(part_id) ON DELETE CASCADE,
  quantity_required INTEGER DEFAULT 1,
  assembly_sequence_order INTEGER
);

CREATE TABLE Part_Reviews (
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  part_id INTEGER NOT NULL REFERENCES Parts(part_id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES Users(user_id),
  fitment_rating INTEGER CHECK (fitment_rating BETWEEN 1 AND 5),
  print_success_rating INTEGER CHECK (print_success_rating BETWEEN 1 AND 5),
  material_used TEXT,
  comment TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE Sessions (
    token_hash TEXT PRIMARY KEY, 
    user_id INTEGER NOT NULL REFERENCES Users(user_id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL, 
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);