#!/usr/bin/env bash
# install.sh
# Automatically install Kaizen skills and modes from roo-skills to Bob
# Usage: Run from anywhere in the project: ./roo-skills/install.sh [target_dir]
# Example: ./roo-skills/install.sh .custom-dir

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory (roo-skills) and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Define paths
SOURCE_SKILLS_DIR="$SCRIPT_DIR/skills"
TARGET_DIR="${1:-$PROJECT_ROOT/.bob}"
TARGET_SKILLS_DIR="$TARGET_DIR/skills"
SOURCE_MODES_FILE="$SCRIPT_DIR/custom_modes.yaml"
TARGET_MODES_FILE="$TARGET_DIR/custom_modes.yaml"

echo -e "${BLUE}=== Kaizen Skills Installer ===${NC}\n"

# Check if source skills directory exists
if [ ! -d "$SOURCE_SKILLS_DIR" ]; then
    echo -e "${RED}Error: source skills directory not found at $SOURCE_SKILLS_DIR${NC}"
    exit 1
fi

# Create target skills directory if it doesn't exist
if [ ! -d "$TARGET_SKILLS_DIR" ]; then
    echo -e "${YELLOW}Creating $TARGET_SKILLS_DIR directory...${NC}"
    mkdir -p "$TARGET_SKILLS_DIR"
fi

# Function to copy a skill
copy_skill() {
    local skill_name=$1
    local source_dir="$SOURCE_SKILLS_DIR/$skill_name"
    local dest_dir="$TARGET_SKILLS_DIR/$skill_name"
    
    if [ ! -d "$source_dir" ]; then
        echo -e "${YELLOW}Warning: Skill '$skill_name' not found in source, skipping...${NC}"
        return 1
    fi
    
    echo -e "${BLUE}Installing skill: $skill_name${NC}"
    
    # Remove existing skill directory if it exists
    if [ -d "$dest_dir" ]; then
        echo -e "${YELLOW}  Removing existing $skill_name...${NC}"
        rm -rf "$dest_dir"
    fi
    
    # Copy the skill
    cp -r "$source_dir" "$dest_dir"
    echo -e "${GREEN}  ✓ Installed $skill_name${NC}"
    
    return 0
}

# Install skills
echo -e "\n${BLUE}Step 1: Installing skills...${NC}"
SKILLS_INSTALLED=0
SKILLS_FAILED=0

for skill_dir in "$SOURCE_SKILLS_DIR"/*; do
    if [ -d "$skill_dir" ]; then
        skill_name=$(basename "$skill_dir")
        if copy_skill "$skill_name"; then
            SKILLS_INSTALLED=$((SKILLS_INSTALLED + 1))
        else
            SKILLS_FAILED=$((SKILLS_FAILED + 1))
        fi
    fi
done

echo -e "\n${GREEN}Installed $SKILLS_INSTALLED skill(s)${NC}"
if [ $SKILLS_FAILED -gt 0 ]; then
    echo -e "${YELLOW}Failed to install $SKILLS_FAILED skill(s)${NC}"
fi

# Install/update custom modes
echo -e "\n${BLUE}Step 2: Installing custom modes...${NC}"

if [ ! -f "$SOURCE_MODES_FILE" ]; then
    echo -e "${YELLOW}Warning: custom_modes.yaml file not found at $SOURCE_MODES_FILE${NC}"
    echo -e "${YELLOW}Skipping modes installation...${NC}"
else
    echo -e "${BLUE}  Merging modes (preserving your existing modes)...${NC}"
    
    # Backup existing file if it exists
    if [ -f "$TARGET_MODES_FILE" ]; then
        backup_file="$TARGET_MODES_FILE.backup.$(date +%Y%m%d_%H%M%S)"
        echo -e "${YELLOW}  Backing up existing custom_modes.yaml to $(basename "$backup_file")${NC}"
        cp "$TARGET_MODES_FILE" "$backup_file"
        
        # Extract kaizen-lite mode from source custom_modes.yaml
        echo -e "${BLUE}  Extracting kaizen-lite mode...${NC}"
        
        # Create temp file with new kaizen-lite mode
        temp_new_mode=$(mktemp)
        awk '/^  - slug: kaizen-lite/ {f=1} /^  - slug:/ && !/kaizen-lite/ {f=0} f' "$SOURCE_MODES_FILE" > "$temp_new_mode"
        
        # Check if kaizen-lite already exists in custom_modes.yaml
        if grep -q "slug: kaizen-lite" "$TARGET_MODES_FILE"; then
            echo -e "${BLUE}  Updating existing kaizen-lite mode...${NC}"
            # Remove old kaizen-lite mode and add new one
            temp_output=$(mktemp)
            
            # Copy everything before kaizen-lite
            awk '/^  - slug: kaizen-lite/{exit} {print}' "$TARGET_MODES_FILE" > "$temp_output"
            
            # Add new kaizen-lite mode
            cat "$temp_new_mode" >> "$temp_output"
            
            # Add everything after old kaizen-lite (skip to next mode or end)
            awk '/^  - slug: kaizen-lite/ {f=1; next} f && /^  - slug:/ {p=1; f=0} p' "$TARGET_MODES_FILE" >> "$temp_output"
            
            mv "$temp_output" "$TARGET_MODES_FILE"
        else
            echo -e "${BLUE}  Adding new kaizen-lite mode...${NC}"
            # Just append the new mode
            cat "$temp_new_mode" >> "$TARGET_MODES_FILE"
        fi
        
        rm "$temp_new_mode"
        echo -e "${GREEN}  ✓ Successfully merged modes${NC}"
    else
        # No existing file, just copy
        echo -e "${BLUE}  Creating new custom_modes.yaml...${NC}"
        cp "$SOURCE_MODES_FILE" "$TARGET_MODES_FILE"
        echo -e "${GREEN}  ✓ Installed custom_modes.yaml${NC}"
    fi
fi

# Verify installation
echo -e "\n${BLUE}Step 3: Verifying installation...${NC}"

# Check skills
echo -e "\n${BLUE}Installed skills in target directory:${NC}"
if [ -d "$TARGET_SKILLS_DIR" ]; then
    for skill_dir in "$TARGET_SKILLS_DIR"/*; do
        if [ -d "$skill_dir" ]; then
            skill_name=$(basename "$skill_dir")
            echo -e "  ${GREEN}✓${NC} $skill_name"
            
            # Check for SKILL.md
            if [ -f "$skill_dir/SKILL.md" ]; then
                echo -e "    - SKILL.md found"
            else
                echo -e "    ${YELLOW}- Warning: SKILL.md not found${NC}"
            fi
            
            # Check for scripts directory
            if [ -d "$skill_dir/scripts" ]; then
                script_count=$(find "$skill_dir/scripts" -type f -name "*.py" | wc -l)
                echo -e "    - $script_count Python script(s) found"
            fi
        fi
    done
else
    echo -e "${RED}Error: .bob/skills directory not found${NC}"
fi

# Check modes file
echo -e "\n${BLUE}Custom modes file:${NC}"
if [ -f "$TARGET_MODES_FILE" ]; then
    echo -e "  ${GREEN}✓${NC} custom_modes.yaml exists"
    mode_count=$(grep -c "slug:" "$TARGET_MODES_FILE" || echo "0")
    echo -e "    - $mode_count mode(s) defined"
else
    echo -e "  ${RED}✗${NC} custom_modes.yaml not found"
fi

# Summary
echo -e "\n${GREEN}=== Installation Complete ===${NC}"
echo -e "${BLUE}Next steps:${NC}"
echo -e "  1. Restart your agent to load the new skills and modes"
echo -e "  2. Verify skills are available in the skill menu"
echo -e "  3. Test the kaizen-lite mode"
echo -e "\n${YELLOW}Note: If you made manual changes to custom_modes.yaml, check the backup file.${NC}"

# Made with Bob
