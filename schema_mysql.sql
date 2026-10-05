CREATE TABLE Users (
    user_id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('consumer','contributor','admin') DEFAULT 'consumer',
    bio TEXT,
    contact_info VARCHAR(255),
    avatar_url VARCHAR(255),
    reputation_score INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE Brands (
    brand_id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL UNIQUE,
    logo_url VARCHAR(255)
);
CREATE TABLE Models (
    model_id INT PRIMARY KEY AUTO_INCREMENT,
    brand_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    picture_url VARCHAR(255),
    FOREIGN KEY (brand_id) REFERENCES Brands(brand_id)
);
CREATE TABLE Vehicle_Generations (
    generation_id INT PRIMARY KEY AUTO_INCREMENT,
    model_id INT NOT NULL,
    generation_name VARCHAR(100),
    start_year INT NOT NULL,
    end_year INT,
    overview_image_url VARCHAR(255),
    FOREIGN KEY (model_id) REFERENCES Models(model_id)
);
CREATE TABLE Parts (
    part_id INT PRIMARY KEY AUTO_INCREMENT,
    sku VARCHAR(64) UNIQUE,
    name VARCHAR(150) NOT NULL,
    description TEXT,
    oem_part_number VARCHAR(100),
    category ENUM('interior','exterior','underhood','trim_clip','lighting_bracket'),
    disassembly_instructions TEXT,
    author_user_id INT NOT NULL,
    license ENUM('CC0','CC-BY-4.0','CC-BY-SA-4.0','CERN-OHL-P-2.0','CERN-OHL-S-2.0') DEFAULT 'CC-BY-SA-4.0',
    forked_from_part_id INT NULL,
    version VARCHAR(20) DEFAULT '1.0.0',
    changelog TEXT,
    is_assembly BOOLEAN DEFAULT FALSE,
    parent_part_id INT NULL,
    is_available_physical BOOLEAN DEFAULT FALSE,
    physical_print_price DECIMAL(8,2) NULL,
    status ENUM('draft','community_tested','verified_fit','flagged') DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (author_user_id) REFERENCES Users(user_id),
    FOREIGN KEY (forked_from_part_id) REFERENCES Parts(part_id) ON DELETE SET NULL,
    FOREIGN KEY (parent_part_id) REFERENCES Parts(part_id) ON DELETE SET NULL
);
CREATE TABLE Part_Compatibilities (
    compatibility_id INT PRIMARY KEY AUTO_INCREMENT,
    part_id INT NOT NULL,
    generation_id INT NOT NULL,
    notes VARCHAR(255),
    hotspot_x DECIMAL(5,2),   -- % from left on the overview image
    hotspot_y DECIMAL(5,2),   -- % from top
    UNIQUE (part_id, generation_id),
    FOREIGN KEY (part_id) REFERENCES Parts(part_id) ON DELETE CASCADE,
    FOREIGN KEY (generation_id) REFERENCES Vehicle_Generations(generation_id) ON DELETE CASCADE
);
CREATE TABLE Part_Files (
    file_id INT PRIMARY KEY AUTO_INCREMENT,
    part_id INT NOT NULL,
    file_type ENUM('mesh_stl','mesh_3mf','cad_step','cad_source','assembly_manual_pdf') NOT NULL,
    r2_object_key VARCHAR(500) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_size_bytes BIGINT UNSIGNED,
    sha256_checksum CHAR(64),
    recommended_material ENUM('PLA','PETG','ABS','ASA','PA_CF','TPU') DEFAULT 'ASA',
    recommended_infill_pct INT DEFAULT 40,
    supports_required BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (part_id) REFERENCES Parts(part_id) ON DELETE CASCADE
);
CREATE TABLE Part_Download_Logs (
    log_id INT PRIMARY KEY AUTO_INCREMENT,
    part_id INT NOT NULL,
    file_id INT NOT NULL,
    user_id INT NULL,
    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ip_country CHAR(2),
    FOREIGN KEY (part_id) REFERENCES Parts(part_id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES Part_Files(file_id) ON DELETE CASCADE
);
CREATE TABLE Cart_Items (
    cart_item_id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    part_id INT NOT NULL,
    quantity INT DEFAULT 1,
    material_printed ENUM('PETG','ASA','PA_CF') DEFAULT 'ASA',
    color VARCHAR(30) DEFAULT 'Black',
    UNIQUE (user_id, part_id),
    FOREIGN KEY (user_id) REFERENCES Users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (part_id) REFERENCES Parts(part_id) ON DELETE CASCADE
);
CREATE TABLE Physical_Orders (
    order_id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    total_amount DECIMAL(10,2) NOT NULL,
    order_status ENUM('paid','printing','shipped','delivered','cancelled') DEFAULT 'paid',
    shipping_address TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES Users(user_id)
);
CREATE TABLE Physical_Order_Items (
    item_id INT PRIMARY KEY AUTO_INCREMENT,
    order_id INT NOT NULL,
    part_id INT NOT NULL,
    quantity INT DEFAULT 1,
    unit_price DECIMAL(8,2) NOT NULL,
    material_printed ENUM('PETG','ASA','PA_CF') NOT NULL,
    color VARCHAR(30) DEFAULT 'Black',
    FOREIGN KEY (order_id) REFERENCES Physical_Orders(order_id) ON DELETE CASCADE,
    FOREIGN KEY (part_id) REFERENCES Parts(part_id)
);
CREATE TABLE Part_Assemblies (
    assembly_id INT PRIMARY KEY AUTO_INCREMENT,
    parent_part_id INT NOT NULL,
    child_part_id INT NOT NULL,
    quantity_required INT DEFAULT 1,
    assembly_sequence_order INT,
    FOREIGN KEY (parent_part_id) REFERENCES Parts(part_id) ON DELETE CASCADE,
    FOREIGN KEY (child_part_id) REFERENCES Parts(part_id) ON DELETE CASCADE
);
CREATE TABLE Part_3D_Specifications (
    spec_id INT PRIMARY KEY AUTO_INCREMENT,
    part_id INT NOT NULL UNIQUE,
    file_format ENUM('STL','STEP','3MF') NOT NULL,
    schematic_file_url VARCHAR(255) NOT NULL,
    recommended_material ENUM('PLA','PETG','ABS','ASA','PA_CF','TPU') DEFAULT 'ASA',
    recommended_infill_percentage INT DEFAULT 40,
    supports_needed BOOLEAN DEFAULT FALSE,
    min_bed_size_x_mm DECIMAL(6,2),
    min_bed_size_y_mm DECIMAL(6,2),
    min_bed_size_z_mm DECIMAL(6,2),
    print_time_est_minutes INT,
    assembly_instructions TEXT,
    FOREIGN KEY (part_id) REFERENCES Parts(part_id) ON DELETE CASCADE
);
CREATE TABLE Part_Reviews (
    review_id INT PRIMARY KEY AUTO_INCREMENT,
    part_id INT NOT NULL,
    user_id INT NOT NULL,
    fitment_rating TINYINT CHECK (fitment_rating BETWEEN 1 AND 5),
    print_success_rating TINYINT CHECK (print_success_rating BETWEEN 1 AND 5),
    material_used VARCHAR(50),
    comment TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (part_id) REFERENCES Parts(part_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES Users(user_id)
);
