"""
Common functionality for Django Template Context Processor for
Online Contextual Help.
"""

import ConfigParser
from django.conf import settings
import logging


log = logging.getLogger(__name__)


def common_doc_url(request, config_file_object):  # pylint: disable=unused-argument
    """
    This function is added in the list of TEMPLATES 'context_processors' OPTION, which is a django setting for
    a tuple of callables that take a request object as their argument and return a dictionary of items
    to be merged into the RequestContext.

    This function returns a dict with get_online_help_info, making it directly available to all mako templates.

    Args:
        request: Currently not used, but is passed by django to context processors.
            May be used in the future for determining the language of choice.
        config_file_object: Configuration file object.
    """

    def get_online_help_info(page_token=None):
        """
        Args:
            page_token: A string that identifies the page for which the help information is requested.
                It should correspond to an option in the docs/config_file_object.ini file.  If it doesn't, the "default"
                option is used instead.

        Returns:
            A dict mapping the following items
                * "doc_url" - a string with the url corresponding to the online help location for the given page_token.
                * "pdf_url" - a string with the url corresponding to the location of the PDF help file.
        """

        def get_config_value_with_default(section_name, option, default_option="default"):
            """
            Args:
                section_name: name of the section in the configuration from which the option should be found
                option: name of the configuration option
                default_option: name of the default configuration option whose value should be returned if the
                    requested option is not found
            """
            if option:
                try:
                    return config_file_object.get(section_name, option)
                except (ConfigParser.NoOptionError, AttributeError):
                    log.debug("Didn't find a configuration option for '%s' section and '%s' option",
                              section_name, option)
            return config_file_object.get(section_name, default_option)

        def get_config_value_with_override_section(section_name, option, override_section_name):
            """
            Args:
                section_name: name of the section in the configuration from which the option should be found
                option: name of the configuration option
                override_section: name of the section in the configuration that may contain an overriding property
            """
            if option:
                try:
                    return config_file_object.get(override_section_name, option)
                except (ConfigParser.NoOptionError, AttributeError):
                    return get_config_value_with_default(section_name, option)

        def get_doc_url():
            """
            Returns:
                The URL for the documentation
            """
            # If the LMS is used for edx.org, return documentation URLs
            # for edX partner documentation. If not, return Open edX
            # documentation. Base URLs will always be different. The
            # document path might be different.
            if settings.USE_EDX_PARTNER_DOCUMENTATION:
                print("I'm edx.org!" + str(settings.USE_EDX_PARTNER_DOCUMENTATION))
                doc_base_url=get_config_value_with_override_section("help_settings", "url_base", "help_settings_edx_partner_overrides")
                doc_page_path=get_config_value_with_override_section("pages", page_token, "pages_edx_partner_overrides")
            else:
                print("I'm Open edX!" + str(settings.USE_EDX_PARTNER_DOCUMENTATION))
                doc_base_url=config_file_object.get("help_settings", "url_base")
                doc_page_path=get_config_value_with_default("pages", page_token)

            # Construct and return the URL for the documentation link.
            return "{url_base}/{language}/{version}/{page_path}".format(
                url_base=doc_base_url,
                language=get_config_value_with_default("locales", settings.LANGUAGE_CODE),
                version=config_file_object.get("help_settings", "version"),
                page_path=doc_page_path,
            )

        def get_pdf_url():
            """
            Returns:
                The URL for the PDF document using the pdf_settings and the help_settings (version) in the configuration
            """
            if settings.USE_EDX_PARTNER_DOCUMENTATION:
                pdf_base_url=get_config_value_with_override_section("help_settings", "url_base", "help_settings_edx_partner_overrides")
                pdf_file_name=get_config_value_with_override_section("pages", page_token, "pages_edx_partner_overrides")
            else:
                pdf_base_url=config_file_object.get("pdf_settings", "pdf_base")
                pdf_file_name=config_file_object.get("pdf_settings", "pdf_file")

            return "{pdf_base}/{version}/{pdf_file}".format(
                pdf_base=pdf_base_url,
                version=config_file_object.get("help_settings", "version"),
                pdf_file=pdf_file_name,
            )

        return {
            "doc_url": get_doc_url(),
            "pdf_url": get_pdf_url(),
        }

    return {'get_online_help_info': get_online_help_info}
