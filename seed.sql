-- SAMPLE DATA ONLY: replace with real catalogue. Fitment/part numbers are illustrative placeholders.
INSERT INTO Users (user_id,username,name,email,password_hash,role,bio) VALUES
 (1,'catalogue_admin','Catalogue Admin','admin@example.invalid','!locked','admin','Seed account owning demo parts.');
INSERT INTO Brands (brand_id,name) VALUES (1,'Toyota'),(2,'BMW'),(3,'Renault'),(4,'Volkswagen');
INSERT INTO Models (model_id,brand_id,name,description) VALUES
 (1,1,'Corolla','Compact family car.'),(2,2,'3 Series','Compact executive car.'),
 (3,3,'Megane','Compact hatchback.'),(4,4,'Golf','Compact hatchback.');
INSERT INTO Vehicle_Generations (generation_id,model_id,generation_name,start_year,end_year,overview_image_url) VALUES
 (1,1,'E120',2001,2007,'/static/car_generic.svg'),
 (2,2,'E46',1998,2006,'/static/car_generic.svg'),
 (3,3,'Megane II',2002,2008,'/static/car_generic.svg'),
 (4,4,'Mk4',1997,2006,'/static/car_generic.svg');
INSERT INTO Parts (part_id,sku,name,description,oem_part_number,category,disassembly_instructions,author_user_id,is_available_physical,physical_print_price,status) VALUES
 (1,'CP-0001','Centre console cupholder insert','Replacement insert for broken cupholder flap.','PLACEHOLDER-001','interior','1. Pull the console trim upward.\n2. Release the two clips.\n3. Lift out the old insert.',1,1,12.50,'community_tested'),
 (2,'CP-0002','Door card trim clip (set of 10)','Push-in clip for door card panels.','PLACEHOLDER-002','trim_clip','1. Slide a trim tool under the door card edge.\n2. Pry gently near each clip.\n3. Pull the old clip out.',1,1,6.00,'verified_fit'),
 (3,'CP-0003','Headlight lower bracket','Printable mounting bracket for headlight lower tab.','PLACEHOLDER-003','lighting_bracket','1. Remove the front bumper cover screws.\n2. Unbolt the headlight.\n3. Remove the broken bracket.',1,0,NULL,'draft'),
 (4,'CP-0004','Engine undertray panel (modular)','Undertray assembled from printable segments.','PLACEHOLDER-004','underhood','Remove all undertray screws and slide out the panel.',1,0,NULL,'community_tested'),
 (5,'CP-0004-A','Undertray segment A','Front segment.',NULL,'underhood',NULL,1,0,NULL,'community_tested'),
 (6,'CP-0004-B','Undertray segment B','Rear segment.',NULL,'underhood',NULL,1,0,NULL,'community_tested');
UPDATE Parts SET is_assembly=1 WHERE part_id=4;
UPDATE Parts SET parent_part_id=4 WHERE part_id IN (5,6);
INSERT INTO Part_Assemblies (parent_part_id,child_part_id,quantity_required,assembly_sequence_order) VALUES (4,5,1,1),(4,6,1,2);
INSERT INTO Part_Compatibilities (part_id,generation_id,notes,hotspot_x,hotspot_y) VALUES
 (1,1,NULL,52,48),(1,2,'Pre-facelift only',52,48),(2,1,NULL,40,42),(2,3,NULL,40,42),(2,4,NULL,40,42),
 (3,2,NULL,88,50),(3,3,NULL,88,50),(4,1,NULL,50,78),(4,4,NULL,50,78);
INSERT INTO Part_3D_Specifications (part_id,file_format,schematic_file_url,recommended_material,recommended_infill_percentage,supports_needed,min_bed_size_x_mm,min_bed_size_y_mm,min_bed_size_z_mm,print_time_est_minutes,assembly_instructions) VALUES
 (1,'STL','part_1.stl','ASA',40,0,90,60,20,95,'Single piece; snap into the console.'),
 (2,'STL','part_2.stl','PETG',60,0,20,20,12,25,'Print 10 at once.'),
 (3,'STL','part_3.stl','ASA',50,1,80,40,30,140,'Bolt to the headlight tab.'),
 (4,'STL','part_4.stl','PA_CF',30,0,220,220,6,0,'Click segments A then B together.');
INSERT INTO Part_Files (part_id,file_type,r2_object_key,file_name,recommended_material,recommended_infill_pct,supports_required) VALUES
 (1,'mesh_stl','schematics/part_1.stl','cupholder_insert_v1.stl','ASA',40,0),
 (2,'mesh_stl','schematics/part_2.stl','door_clip_v1.stl','PETG',60,0),
 (3,'mesh_stl','schematics/part_3.stl','headlight_bracket_v1.stl','ASA',50,1),
 (4,'mesh_stl','schematics/part_4.stl','undertray_v1.stl','PA_CF',30,0);
