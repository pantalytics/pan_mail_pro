/** @odoo-module */
// Odoo's own AttachmentList, with the props each series wants: `attachments`
// and `unlinkAttachment` up to 19, `attachmentGroups` and `unlinkAttachments`
// on 20. The template is the half that differs (tools/series/20.0 carries
// the 20 one); this class serves both.
import { Component } from "@odoo/owl";
import { AttachmentList } from "@mail/core/common/attachment_list";
import { defineProps, useProps } from "@pan_mail_pro/compat/owl";

export class MailProAttachmentList extends Component {
    static template = "pan_mail_pro.AttachmentList";
    static components = { AttachmentList };
    static propsSchema = {
        attachments: Array,
        unlinkAttachment: Function,
    };

    setup() {
        this.props = useProps();
    }

    /**
     * 18's list wants the height its images are drawn at (`imagesHeight`,
     * required; without it the image asks the server for `height=undefined`
     * and gets a 500); 19's list has no such prop. Read off the component.
     */
    get listProps() {
        const props = {
            attachments: this.props.attachments,
            unlinkAttachment: (attachment) => this.props.unlinkAttachment(attachment),
        };
        if (AttachmentList.props?.includes("imagesHeight")) {
            props.imagesHeight = 160;
        }
        return props;
    }

    /** 20 groups duplicates under one card; this screen has no duplicates to fold. */
    get groups() {
        return this.props.attachments.map((attachment) => ({ attachment, duplicates: [] }));
    }

    unlinkMany(attachments) {
        return Promise.all(attachments.map((attachment) => this.props.unlinkAttachment(attachment)));
    }
}
defineProps(MailProAttachmentList);
