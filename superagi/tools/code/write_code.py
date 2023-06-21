from typing import Type, Optional, List

from pydantic import BaseModel, Field
from superagi.config.config import get_config
from superagi.agent.agent_prompt_builder import AgentPromptBuilder
import os
from superagi.llms.base_llm import BaseLlm
from superagi.tools.base_tool import BaseTool
from superagi.lib.logger import logger
from superagi.models.db import connect_db
from superagi.helper.resource_helper import ResourceHelper
from superagi.helper.s3_helper import S3Helper
from sqlalchemy.orm import sessionmaker
import re

class CodingSchema(BaseModel):
    spec_description: str = Field(
        ...,
        description="Specification for generating code which is generated by WriteSpecTool",
    )
class CodingTool(BaseTool):
    """
    Used to generate code.

    Attributes:
        llm: LLM used for code generation.
        name : The name of tool.
        description : The description of tool.
        args_schema : The args schema.
        goals : The goals.
    """
    llm: Optional[BaseLlm] = None
    agent_id: int = None
    name = "CodingTool"
    description = (
        "You will get instructions for code to write. You will write a very long answer. "
        "Make sure that every detail of the architecture is, in the end, implemented as code. "
        "Think step by step and reason yourself to the right decisions to make sure we get it right. "
        "You will first lay out the names of the core classes, functions, methods that will be necessary, "
        "as well as a quick comment on their purpose. Then you will output the content of each file including ALL code."
    )
    args_schema: Type[CodingSchema] = CodingSchema
    goals: List[str] = []

    class Config:
        arbitrary_types_allowed = True

        
    def write_codes_to_file(self, codes_content: str, code_file_name: str) -> str:
        """
        Write the generated codes to the specified file.

        Args:
            codes_content: The content (code) of the code.
            code_file_name: Name of the file where the code will be written.

        Returns:
            A string indicating if the codes were saved successfully or an error message.
        """
        try:
            engine = connect_db()
            Session = sessionmaker(bind=engine)
            session = Session()

            final_path = code_file_name
            root_dir = get_config('RESOURCES_OUTPUT_ROOT_DIR')
            if root_dir is not None:
                root_dir = root_dir if root_dir.startswith("/") else os.getcwd() + "/" + root_dir
                root_dir = root_dir if root_dir.endswith("/") else root_dir + "/"
                final_path = root_dir + code_file_name
            else:
                final_path = os.getcwd() + "/" + code_file_name

            with open(final_path, mode="w") as code_file:
                code_file.write(codes_content)

            with open(final_path, 'r') as code_file:
                resource = ResourceHelper.make_written_file_resource(file_name=code_file_name,
                                                                    agent_id=self.agent_id, file=code_file, channel="OUTPUT")

            if resource is not None:
                session.add(resource)
                session.commit()
                session.flush()
                if resource.storage_type == "S3":
                    s3_helper = S3Helper()
                    s3_helper.upload_file(code_file, path=resource.path)
                    
            logger.info(f"Code {code_file_name} saved successfully")
            session.close()
            return "Codes saved successfully"
        except Exception as e:
            session.close()
            return f"Error saving codes to file: {e}"
    
    def _execute(self, spec_description: str) -> str:
        """
        Execute the write_code tool.

        Args:
            spec_description : The specification description.
            code_file_name: The name of the file where the generated codes will be saved.

        Returns:
            Generated codes files or error message.
        """
        try:
            prompt = """You are a super smart developer who practices good Development for writing code according to a specification.

            Your high-level goal is:
            {goals}

            Use this specs for generating the code:
            {spec}

            You will get instructions for code to write.
            You will write a very long answer. Make sure that every detail of the architecture is, in the end, implemented as code.
            Make sure that every detail of the architecture is, in the end, implemented as code.

            Think step by step and reason yourself to the right decisions to make sure we get it right.
            You will first lay out the names of the core classes, functions, methods that will be necessary, as well as a quick comment on their purpose.

            Then you will output the content of each file including ALL code.
            Each file must strictly follow a markdown code block format, where the following tokens must be replaced such that
            [FILENAME] is the lowercase file name including the file extension,
            [LANG] is the markup code block language for the code's language, and [CODE] is the code:

            [FILENAME]
            ```[LANG]
            [CODE]
            ```

            You will start with the "entrypoint" file, then go to the ones that are imported by that file, and so on.
            Please note that the code should be fully functional. No placeholders.

            Follow a language and framework appropriate best practice file naming convention.
            Make sure that files contain all imports, types etc. Make sure that code in different files are compatible with each other.
            Ensure to implement all code, if you are unsure, write a plausible implementation.
            Include module dependency or package manager dependency definition file.
            Before you finish, double check that all parts of the architecture is present in the files.
            """
            prompt = prompt.replace("{goals}", AgentPromptBuilder.add_list_items_to_string(self.goals))
            prompt = prompt.replace("{spec}", spec_description)
            messages = [{"role": "system", "content": prompt}]
            
            result = self.llm.chat_completion(messages, max_tokens=self.max_token_limit)

            # Get all filenames and corresponding code blocks
            regex = r"(\S+?)\n```\S+\n(.+?)```"
            matches = re.finditer(regex, result["content"], re.DOTALL)

            # Save each file
            for match in matches:
                # Get the filename
                file_name = re.sub(r'[<>"|?*]', "", match.group(1))

                # Get the code
                code = match.group(2)

                # Ensure file_name is not empty
                if file_name.strip():
                    save_result = self.write_codes_to_file(code, file_name)
                    if save_result.startswith("Error"):
                        return save_result

            # Get README contents and save
            split_result = result["content"].split("```")
            if len(split_result) > 0:
                readme = split_result[0]
                save_readme_result = self.write_codes_to_file(readme, "README.md")
                if save_readme_result.startswith("Error"):
                    return save_readme_result

            return "codes generated and saved successfully"
        except Exception as e:
            logger.error(e)
            return f"Error generating codes: {e}"